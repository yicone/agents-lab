import assert from "node:assert/strict";
import test from "node:test";
import { createPlanReads } from "./plan-reads.js";

const ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function blankState() {
  return {
    planList: [],
    planListLoaded: false,
    planListLoading: false,
    planListComplete: true,
    planDirectoryError: null,
    planListError: "",
    planDetails: new Map(),
    planDetailErrors: new Map(),
    planDetailLoading: new Set(),
  };
}

const list = { view: "agents", planPage: "list", planId: "", fromPlan: "" };
const home = { view: "agents", planPage: "", planId: "", fromPlan: "" };
const review = { view: "agents", planPage: "review", planId: ID, fromPlan: "" };

function harness({ listBody, detailBody }) {
  const state = blankState();
  let route = home;
  let listGets = 0;
  let detailGets = 0;
  const reads = createPlanReads(state, {
    fetchList: async () => {
      listGets += 1;
      if (listBody instanceof Error) throw listBody;
      return listBody;
    },
    fetchDetail: async () => {
      detailGets += 1;
      const body = typeof detailBody === "function" ? detailBody(detailGets) : detailBody;
      if (body instanceof Error) throw body;
      return body;
    },
    onUpdate: () => reads.sync(route),
  });
  return {
    state,
    reads,
    counts: () => ({ listGets, detailGets }),
    async enter(next) {
      route = next;
      await reads.sync(next);
    },
  };
}

const listOk = { plans: [{ plan_id: ID }], complete: true, directory_error: null };
const detailOk = { ok: true, plan: { plan_id: ID, mark: "cached" } };

test("re-entering the plan list fetches again and a repeat render does not", async () => {
  const page = harness({ listBody: listOk, detailBody: detailOk });
  await page.enter(list);
  assert.equal(page.counts().listGets, 1);
  await page.enter(list);
  assert.equal(page.counts().listGets, 1);
  await page.enter(home);
  await page.enter(list);
  assert.ok(page.counts().listGets > 1);
});

test("a successful plan detail stays cached for the visit and refreshes on the next visit", async () => {
  let release = () => {};
  const page = harness({
    listBody: listOk,
    detailBody: (n) => {
      if (n === 1) return detailOk;
      return new Promise((resolve) => {
        release = () => resolve({ ok: true, plan: { plan_id: ID, mark: "fresh" } });
      });
    },
  });
  await page.enter(review);
  assert.equal(page.counts().detailGets, 1);
  assert.equal(page.state.planDetails.get(ID).mark, "cached");
  await page.enter(review);
  assert.equal(page.counts().detailGets, 1);
  await page.enter(home);
  const pending = page.enter(review);
  assert.equal(page.counts().detailGets, 2);
  assert.equal(page.state.planDetails.get(ID).mark, "cached");
  release();
  await pending;
  assert.equal(page.state.planDetails.get(ID).mark, "fresh");
});

test("a failed plan detail is fetched again when the review route is entered again", async () => {
  const page = harness({
    listBody: listOk,
    detailBody: () => new Error("plan: 404 missing"),
  });
  await page.enter(review);
  assert.equal(page.counts().detailGets, 1);
  assert.match(page.state.planDetailErrors.get(ID), /404/);
  await page.enter(review);
  assert.equal(page.counts().detailGets, 1);
  await page.enter(home);
  await page.enter(review);
  assert.ok(page.counts().detailGets > 1);
});
