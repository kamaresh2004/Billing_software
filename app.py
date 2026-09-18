"""Render compatibility entrypoint for the nested billing-flask application."""
import importlib.util
import os
import sys


PROJECT_DIR = os.path.join(os.path.dirname(__file__), "billing-flask")
sys.path.insert(0, PROJECT_DIR)
module_spec = importlib.util.spec_from_file_location("billing_app", os.path.join(PROJECT_DIR, "app.py"))
billing_app = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(billing_app)

app = billing_app.app

with app.app_context():
    billing_app.db.create_all()
    billing_app.seed_data()
