.PHONY: db-gen db-gen-check test

# Regenerate the drift canary from a database. See scripts/gen_models.sh for
# which database, and why the answer is not automatically the live one.
db-gen:
	scripts/gen_models.sh

# What CI runs. Regenerates and fails if the result is not what is committed.
db-gen-check: db-gen
	git diff --exit-code app/db/_generated_models.py

test:
	.venv/bin/python -m pytest -q
