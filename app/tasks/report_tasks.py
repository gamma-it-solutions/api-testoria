# TODO: Async report generation via Celery
#
# When reports become too slow to generate synchronously (large runs with
# thousands of results), move PDF/Excel generation into Celery tasks.
#
# Planned tasks:
#   - generate_run_report_pdf_task(run_id: int) -> stores PDF in UPLOAD_DIR
#   - generate_run_report_excel_task(run_id: int) -> stores Excel in UPLOAD_DIR
#   - generate_custom_report_task(filters: dict) -> stores result in UPLOAD_DIR
#
# The router would return a 202 with a task ID, and the client would poll
# GET /reports/tasks/{task_id} for status + download link.
