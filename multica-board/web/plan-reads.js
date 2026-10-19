/** Read-only plan list and detail fetches. No writes. */

export function routeKey(route) {
  return [route?.view || "", route?.planPage || "", route?.planId || "", route?.fromPlan || ""].join("\0");
}

export function detailTarget(route) {
  if (route?.view !== "agents") return "";
  if (route.planPage === "review") return route.planId || "";
  if (route.planPage === "new" && route.fromPlan) return route.fromPlan;
  return "";
}

/**
 * List: GET when the list route is entered, not on later renders of that visit.
 * Detail: a successful read stays on screen, and the next visit GETs again.
 * A failed read is not a reason to skip that next visit.
 */
export function createPlanReads(state, { fetchList, fetchDetail, onUpdate }) {
  let key = "";
  let epoch = 0;
  let listEpoch = 0;
  let listGen = 0;
  const detailEpoch = new Map();
  const detailGen = new Map();

  function note(route) {
    const next = routeKey(route);
    if (next === key) return;
    key = next;
    epoch += 1;
  }

  async function loadList() {
    const gen = ++listGen;
    state.planListLoading = true;
    try {
      const mapped = await fetchList();
      if (gen !== listGen) return;
      state.planList = mapped.plans;
      state.planListComplete = mapped.complete;
      state.planDirectoryError = mapped.directory_error;
      state.planListError = "";
      state.planListLoaded = true;
    } catch (err) {
      if (gen !== listGen) return;
      state.planListError = String(err.message || err);
      state.planListLoaded = true;
      if (!state.planList?.length) state.planListComplete = false;
    } finally {
      if (gen === listGen) {
        state.planListLoading = false;
        onUpdate?.();
      }
    }
  }

  async function loadDetail(id) {
    const gen = (detailGen.get(id) || 0) + 1;
    detailGen.set(id, gen);
    state.planDetailLoading.add(id);
    state.planDetailErrors.delete(id);
    try {
      const mapped = await fetchDetail(id);
      if (detailGen.get(id) !== gen) return;
      if (!mapped?.ok) state.planDetailErrors.set(id, mapped?.error || "计划无法读取");
      else {
        state.planDetails.set(id, mapped.plan);
        state.planDetailErrors.delete(id);
      }
    } catch (err) {
      if (detailGen.get(id) !== gen) return;
      state.planDetailErrors.set(id, String(err.message || err));
    } finally {
      if (detailGen.get(id) === gen) {
        state.planDetailLoading.delete(id);
        onUpdate?.();
      }
    }
  }

  function sync(route) {
    note(route);
    const jobs = [];
    if (route?.view === "agents" && route.planPage === "list" && listEpoch !== epoch) {
      listEpoch = epoch;
      jobs.push(loadList());
    }
    const id = detailTarget(route);
    if (id && detailEpoch.get(id) !== epoch) {
      detailEpoch.set(id, epoch);
      jobs.push(loadDetail(id));
    }
    return Promise.all(jobs);
  }

  return { sync, refreshList: loadList };
}
