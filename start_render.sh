#!/usr/bin/env bash
set -e
cd billing-flask
python init_db.py
gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 2 'app:create_app()'
