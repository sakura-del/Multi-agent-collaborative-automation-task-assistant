# 多智能体协作自动化任务助手 - Nginx 前端镜像
# Plan 1 Task 6：在镜像内构建前端，容器不再依赖宿主机 frontend/dist 挂载
# build context 必须是仓库根（compose 指定）

FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.25-alpine
COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend /frontend/dist /usr/share/nginx/html
