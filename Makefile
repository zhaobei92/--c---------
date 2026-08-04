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

# 集成测试(PostgreSQL/Redis/MinIO):服务端 P0 整改阶段接入 pytest -m integration
verify-integration: up
	@echo "TODO(第二批): cd server && python3 -m pytest -q -m integration"

up:
	docker compose up -d --wait

down:
	docker compose down -v
