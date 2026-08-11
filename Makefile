# 统一验证入口。CI 与本地都以 `make verify` 为准,任何失败都会终止(无忽略)。
.PHONY: verify verify-server verify-mock verify-flutter verify-integration up down

verify: verify-server verify-mock verify-flutter
	@echo "== ALL VERIFICATIONS PASSED =="

verify-server:
	python3 -m compileall -q server/app mock_device
	cd server && python3 -m pytest -q

verify-mock:
	python3 -m pytest mock_device/tests -q

verify-flutter:
	cd mobile && flutter pub get && flutter gen-l10n && flutter analyze && flutter test

# 集成测试(PostgreSQL/Redis/MinIO):第二批实现 pytest -m integration。
# 实现前显式失败,禁止"假绿"——绿色只能来自真实执行的测试。
verify-integration: up
	@echo "ERROR: integration tests not implemented yet (第二批: SQLAlchemy Repository + Redis ACK + MinIO 上传)" >&2
	@exit 1

# 仅启动依赖环境(不跑测试),供本地手工联调
integration-env-up: up

up:
	docker compose up -d --wait

down:
	docker compose down -v
