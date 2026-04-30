# 部署指南

## 架构

```
用户浏览器 → Vercel (前端静态文件)
              ↘ /api 请求代理 → 云服务器 (FastAPI 后端)
                                    ↓
                               SQLite (Docker Volume)
```

## 后端部署（云服务器）

### 方式一：Docker（推荐）

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

### 方式二：直接运行

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CORS_ORIGINS` | `*` | 允许的前端域名，多个用逗号分隔 |

生产环境建议设置：
```bash
export CORS_ORIGINS=https://your-app.vercel.app
```

## 前端部署（Vercel）

### 1. 推送代码到 GitHub

```bash
git add .
git commit -m "ready for deploy"
git push origin master
```

### 2. Vercel 导入

1. 打开 https://vercel.com/new
2. 导入 GitHub 仓库
3. **Root Directory** 设为 `frontend`
4. **Environment Variables** 添加：
   - `VITE_API_URL` = 不填（使用默认 `/api`，由 Vercel rewrites 代理）
5. **Vercel 项目设置** → Environment Variables 添加：
   - `BACKEND_URL` = `https://your-backend.com`（你的后端地址，不含 /api）

### vercel.json 说明

`vercel.json` 中的 rewrites 规则会将 `/api/*` 请求转发到 `${BACKEND_URL}/api/*`。
`BACKEND_URL` 需要在 Vercel 环境变量中设置。

## 完整流程示例

假设后端部署在 `https://stock-api.example.com`：

1. 云服务器上 `docker compose up -d` 启动后端
2. GitHub 推送代码
3. Vercel 导入仓库，Root Directory = `frontend`
4. Vercel 环境变量设置 `BACKEND_URL=https://stock-api.example.com`
5. 部署完成后访问 Vercel 分配的域名

## 数据库持久化

SQLite 数据文件在 `./data/stock_sim.db`，Docker 方式通过 volume 映射到宿主机。
备份：`cp data/stock_sim.db data/stock_sim_$(date +%Y%m%d).db`
