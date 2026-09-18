from app import app, seed_data
from models import db


with app.app_context():
    db.create_all()
    seed_data()
    print("Database initialized and seed data verified.")
