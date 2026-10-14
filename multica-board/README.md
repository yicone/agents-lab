# Multica Board

本地只读、跨 workspace 的 Multica 看板（ACM-161 第一阶段）。只监听 `127.0.0.1`，没有任何写接口。

- `web/`：前端静态页面（无构建、无依赖），由后端在 `/` 托管。
- `web/fixtures/`：契约样例响应（虚构数据），`generate.py` 重新生成。
- `docs/api-contract.md`：前后端只读 API 契约。
- `docs/frontend-design.md`：信息架构与交互设计。

只看前端（mock 数据）：

```sh
python3 -m http.server --bind 127.0.0.1 8765 -d multica-board/web
# 打开 http://127.0.0.1:8765/?mock=1
```
