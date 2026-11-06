# Makefile（在项目根目录）
.PHONY: infra sandbox up down logs

# 构建执行镜像（含 mvn/node/java/playwright，体积大，变更少）
sandbox:
	docker build -t sandbox-maven-node:latest -f executor/Dockerfile.sandbox .

# 构建 executor 服务镜像
infra:
	docker build -t executor-service:latest -f executor/Dockerfile .

# 一键起全套（先确保镜像已构建）
up:
	docker compose -f docker-compose.infra.yml up -d

down:
	docker compose -f docker-compose.infra.yml down

logs:
	docker compose -f docker-compose.infra.yml logs -f