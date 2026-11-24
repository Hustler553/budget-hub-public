from budget import config, create_app, start_scheduler
from budget.services import recurring, vault
from budget import db

app = create_app()

if __name__ == "__main__":
    try:
        recurring.post_due_recurring()
    except Exception as exc:
        print(f"Warning: posting recurring items failed: {exc}")
    try:
        db.backup_db()
    except Exception as exc:
        print(f"Warning: database backup failed: {exc}")
    start_scheduler()
    config.warn_if_exposed()
    vault.warn_if_key_is_weak()
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)
