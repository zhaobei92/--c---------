# 统一验证入口。CI 与本地都以 `make verify` 为准,任何失败都会终止(无忽略)。
.PHONY: verify verify-server verify-mock verify-flutter verify-integration up down demo-up demo-down

verify: verify-server verify-mock verify-flutter
	@echo "== ALL VERIFICATIONS PASSED =="

verify-server:
	python3 -m compileall -q server/app mock_device
	cd server && python3 -m pytest -q

verify-mock:
	python3 -m pytest mock_device/tests -q

verify-flutter:
	cd mobile && flutter pub get && flutter gen-l10n && flutter analyze && flutter test

# 集成测试:真实 PostgreSQL + Redis + S3(MinIO/moto)+ SMTP(Mailpit)。
# 迁移在测试夹具内执行(空库 → alembic upgrade head)。
# 环境变量可覆盖端点(YS_DATABASE_URL / YS_REDIS_URL / YS_S3_ENDPOINT /
# YS_SMTP_HOST / MAILPIT_API);SKIP_COMPOSE=1 时不启动 docker compose
# (CI 的 service 容器或本地原生服务场景)。
verify-integration:
ifndef SKIP_COMPOSE
	docker compose up -d --wait
endif
	cd server && python3 -m pytest -m integration -q

# 仅启动依赖环境(不跑测试),供本地手工联调
integration-env-up: up

# Demo 环境:基础设施 + API + Worker + 模拟录音机(供人工演示/审核录屏)
demo-up:
	scripts/demo_up.sh

demo-down:
	scripts/demo_up.sh --down

up:
	docker compose up -d --wait

down:
	docker compose down -v
