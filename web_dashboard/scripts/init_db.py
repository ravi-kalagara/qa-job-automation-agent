import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.application_tracker import ApplicationTracker

def init_database():
    """Initialize the application database"""
    print("Initializing database...")
    
    default_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
    db_path = os.environ.get('DATABASE_PATH') or os.path.join(
        os.environ.get('DATA_DIR', default_dir), 'applications.db')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    tracker = ApplicationTracker(db_path)
    
    print(f"Database initialized at: {db_path}")
    tracker.close()

if __name__ == '__main__':
    init_database()
    print("Database setup complete!")
