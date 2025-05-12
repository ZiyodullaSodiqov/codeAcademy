from flask import Flask, request, jsonify
from flask_pymongo import PyMongo
from bson import ObjectId
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv
from flask_cors import CORS
import tempfile
import subprocess
import time
from flask_bcrypt import Bcrypt
import jwt
from functools import wraps
import shutil
import logging
import re
import sys

from pymongo.errors import ConnectionFailure
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load configurations
load_dotenv('.env')

app = Flask(__name__)

# Configure CORS https://codeacademy.nordicuniversity.org
CORS(app, resources={
    r"/api/*": {
        "origins": "*",  # Allow all origins
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(32).hex())
app.config["MONGO_URI"] = os.getenv("MONGO_URI", "mongodb+srv://ziyodullasodiqov01:HZL53G_Cgni3NT3@cluster0.vfh7g.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")

mongo = PyMongo(app)
bcrypt = Bcrypt(app)

# Language configurations
LANGUAGE_CONFIG = {
    'python': {
        'extension': '.py',
        'command': 'python3',
        'compile': None,
        'run': lambda filename: [filename],
        'sanitize': lambda code: re.sub(r'import\s+(os|subprocess|sys|shutil|socket|threading)', '#', code)
    },
    'java': {
        'extension': '.java',
        'command': 'java',
        'compile': lambda filename: ['javac', filename],
        'run': lambda classname: ['java', '-cp', os.path.dirname(classname), os.path.basename(classname).replace('.java', '')],
        'sanitize': lambda code: code
    },
    'cpp': {
        'extension': '.cpp',
        'command': 'g++',
        'compile': lambda filename: ['g++', filename, '-o', filename.replace('.cpp', '')],
        'run': lambda filename: [filename.replace('.cpp', '')],
        'sanitize': lambda code: code
    },
    'javascript': {
        'extension': '.js',
        'command': 'node',
        'compile': None,
        'run': lambda filename: ['node', filename],
        'sanitize': lambda code: code
    }
}
max_retries = 3
retry_delay = 5  # seconds

for attempt in range(max_retries):
    try:
        mongo.cx.server_info()  # Test connection
        problems_col = mongo.db.problems
        submissions_col = mongo.db.submissions
        users_col = mongo.db.users
        olympiads_col = mongo.db.olympiads
        olympiad_participants_col = mongo.db.olympiad_participants
        logger.info("✅ MongoDB connected and collections initialized successfully")
        break
    except ConnectionFailure as e:
        logger.warning(f"⚠️ MongoDB connection attempt {attempt + 1} failed: {str(e)}")
        if attempt == max_retries - 1:
            logger.error(f"❌ FATAL: Could not connect to MongoDB after {max_retries} attempts")
            sys.exit(1)
        time.sleep(retry_delay)

# ====================== AUTH MIDDLEWARE ======================
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            try:
                token = request.headers['Authorization'].split(" ")[1]
            except IndexError:
                return jsonify({'error': 'Invalid Authorization header format'}), 401

        if not token:
            return jsonify({'error': 'Token is missing'}), 401

        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = users_col.find_one({'_id': ObjectId(data['user_id'])})
            if not current_user:
                return jsonify({'error': 'User not found'}), 404
            kwargs['current_user'] = current_user
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token is invalid'}), 401

        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        current_user = kwargs.get('current_user')
        if current_user.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ====================== CODE EXECUTION ======================
def execute_code(code, language, input_data, time_limit):
    """Execute code in the specified language with given input"""
    lang_config = LANGUAGE_CONFIG.get(language.lower())
    if not lang_config:
        raise ValueError(f"Unsupported language: {language}")

    # Sanitize code
    sanitized_code = lang_config['sanitize'](code)
    
    temp_dir = tempfile.mkdtemp()
    try:
        # Create source file
        filename = os.path.join(temp_dir, f"source{lang_config['extension']}")
        with open(filename, 'w') as f:
            f.write(sanitized_code)

        # Compile if needed
        if lang_config['compile']:
            compile_proc = subprocess.run(
                lang_config['compile'](filename),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=time_limit
            )
            if compile_proc.returncode != 0:
                return {
                    'status': 'Compilation Error',
                    'error': compile_proc.stderr,
                    'runtime': 0
                }

        # Run the program
        start_time = time.time()
        run_command = lang_config['run'](filename)
        process = subprocess.Popen(
            run_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=temp_dir
        )

        try:
            stdout, stderr = process.communicate(
                input=input_data,
                timeout=time_limit
            )
            runtime = time.time() - start_time

            if process.returncode != 0:
                return {
                    'status': 'Runtime Error',
                    'output': stdout,
                    'error': stderr,
                    'runtime': runtime
                }

            return {
                'status': 'Accepted',
                'output': stdout.strip(),
                'runtime': runtime
            }

        except subprocess.TimeoutExpired:
            process.kill()
            return {
                'status': 'Time Timeout Exceeded',
                'runtime': time_limit
            }

    except Exception as e:
        return {
            'status': 'Evaluation Error',
            'error': str(e),
            'runtime': 0
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# ====================== SUBMISSION PROCESSING ======================
def process_submission(code, language, problem, user_id, is_olympiad=False, olympiad_id=None, participation=None):
    """Process code submission for a problem or olympiad"""
    submission = {
        'problem_id': problem['id'],
        'user_id': ObjectId(user_id),
        'code': code,
        'language': language.lower(),
        'submitted_at': datetime.now(timezone.utc),
        'status': 'Pending',
        'results': []
    }
    if is_olympiad:
        submission['olympiad_id'] = ObjectId(olympiad_id)

    submission_id = submissions_col.insert_one(submission).inserted_id
    test_results = []
    is_correct = True
    max_runtime = 0
    execution_start = datetime.now(timezone.utc)

    for test_case in problem.get('test_cases', []):
        try:
            result = execute_code(
                code=code,
                language=language,
                input_data=test_case['input'],
                time_limit=problem.get('time_limit', 2)
            )

            test_result = {
                'input': test_case['input'],
                'expected': test_case['output'],
                'actual': result.get('output', ''),
                'runtime': result.get('runtime', 0),
                'status': result['status']
            }

            if 'error' in result:
                test_result['error'] = result['error']

            if result['status'] != 'Accepted' or result.get('output', '') != test_case['output']:
                is_correct = False

            max_runtime = max(max_runtime, result.get('runtime', 0))
            test_results.append(test_result)

        except Exception as e:
            test_results.append({
                'input': test_case['input'],
                'expected': test_case['output'],
                'status': 'Evaluation Error',
                'error': str(e),
                'runtime': 0
            })
            is_correct = False

    final_status = 'Accepted' if is_correct else 'Rejected'
    submissions_col.update_one(
        {'_id': submission_id},
        {'$set': {
            'status': final_status,
            'results': test_results,
            'runtime': max_runtime
        }}
    )

    points_earned = 0
    time_taken = (datetime.now(timezone.utc) - execution_start).total_seconds()

    if is_correct:
        base_points = {
            'easy': 100,
            'medium': 200,
            'hard': 300
        }.get(problem['difficulty'].lower(), 100)
        points_earned = 10 if not is_olympiad else max(10, base_points - int(time_taken))

        if is_olympiad:
            olympiad_participants_col.update_one(
                {'_id': participation['_id']},
                {
                    '$push': {
                        'problems_solved': {
                            'problem_id': problem['id'],
                            'solved_at': datetime.now(timezone.utc),
                            'time_taken': time_taken,
                            'points_earned': points_earned
                        }
                    },
                    '$inc': {'total_points': points_earned}
                }
            )
        else:
            users_col.update_one(
                {'_id': ObjectId(user_id)},
                {
                    '$addToSet': {'solved_problems': problem['id']},
                    '$inc': {'total_points': points_earned}
                }
            )

        problems_col.update_one(
            {'id': problem['id']},
            {'$inc': {'solved_count': 1}}
        )

    return {
        'submission_id': str(submission_id),
        'problem_id': problem['id'],
        'status': final_status,
        'results': test_results,
        'points_earned': points_earned,
        'time_taken': time_taken,
        'runtime': max_runtime,
        'is_correct': is_correct
    }

# ====================== PROBLEM ENDPOINTS ======================
@app.route('/api/problems/<problem_id>/submit', methods=['POST'])
@token_required
def submit_problem_solution(current_user, problem_id):
    try:
        data = request.get_json()
        required_fields = ['code', 'language']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        problem = problems_col.find_one({'id': problem_id})
        if not problem:
            return jsonify({'error': 'Problem not found'}), 404

        result = process_submission(
            code=data['code'],
            language=data['language'],
            problem=problem,
            user_id=str(current_user['_id'])
        )
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in submit_problem_solution: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/problems/<problem_id>', methods=['GET'])
def get_problem(problem_id):
    try:
        problem = problems_col.find_one({'id': problem_id}, {'_id': 0})
        if not problem:
            return jsonify({'error': 'Problem not found'}), 404
        return jsonify(problem)
    except Exception as e:
        logger.error(f"Error in get_problem: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/problems', methods=['GET'])
def get_all_problems():
    try:
        problems = list(problems_col.find({}, {'_id': 0}))
        return jsonify(problems)
    except Exception as e:
        logger.error(f"Error in get_all_problems: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/problems', methods=['POST'])
@token_required
@admin_required
def create_problem(current_user):
    try:
        data = request.get_json()
        required_fields = ['id', 'title', 'description', 'difficulty', 'test_cases']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        if problems_col.find_one({'id': data['id']}):
            return jsonify({'error': 'Problem ID already exists'}), 400

        problem = {
            'id': data['id'],
            'title': data['title'],
            'description': data['description'],
            'difficulty': data['difficulty'],
            'tags': data.get('tags', []),
            'time_limit': data.get('time_limit', 2),
            'memory_limit': data.get('memory_limit', 256),
            'test_cases': data['test_cases'],
            'created_at': datetime.utcnow(),
            'created_by': str(current_user['_id']),
            'solved_count': 0
        }

        problems_col.insert_one(problem)
        return jsonify({'message': 'Problem created successfully'}), 201

    except Exception as e:
        logger.error(f"Error in create_problem: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/problems/<problem_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_problem(current_user, problem_id):
    try:
        result = problems_col.delete_one({'id': problem_id})
        if result.deleted_count == 0:
            return jsonify({'error': 'Problem not found'}), 404
        return jsonify({'message': 'Problem deleted successfully'})
    except Exception as e:
        logger.error(f"Error in delete_problem: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ====================== OLYMPIAD ENDPOINTS ======================
@app.route('/api/olympiads/<olympiad_id>', methods=['GET'])
def get_olympiad(olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        olympiad['_id'] = str(olympiad['_id'])
        olympiad['created_by'] = str(olympiad['created_by'])
        olympiad['start_time'] = olympiad['start_time'].isoformat()
        olympiad['end_time'] = olympiad['end_time'].isoformat()
        olympiad['created_at'] = olympiad['created_at'].isoformat()
        return jsonify(olympiad)

    except Exception as e:
        logger.error(f"Error in get_olympiad: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/problems', methods=['GET'])
def get_olympiad_problems(olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        problems = list(problems_col.find(
            {'id': {'$in': olympiad['problems']}},
            {'_id': 0}
        ))
        return jsonify(problems)

    except Exception as e:
        logger.error(f"Error in get_olympiad_problems: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/check-registration', methods=['GET'])
@token_required
def check_registration(current_user, olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        existing = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        return jsonify({
            'isRegistered': bool(existing),
            'olympiadStatus': olympiad.get('status', 'upcoming')
        })
    except Exception as e:
        logger.error(f"Error in check_registration: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads', methods=['GET'])
def get_all_olympiads():
    try:
        olympiads = list(olympiads_col.find({}))
        for olympiad in olympiads:
            olympiad['_id'] = str(olympiad['_id'])
        return jsonify(olympiads)
    except Exception as e:
        logger.error(f"Error in get_all_olympiads: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/olympiads', methods=['POST'])
@token_required
@admin_required
def create_olympiad(current_user):
    try:
        data = request.get_json()
        required_fields = ['name', 'start_time', 'end_time', 'problems']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        olympiad = {
            'name': data['name'],
            'description': data.get('description', ''),
            'start_time': datetime.fromisoformat(data['start_time']),
            'end_time': datetime.fromisoformat(data['end_time']),
            'problems': data['problems'],
            'created_at': datetime.utcnow(),
            'created_by': str(current_user['_id'])
        }

        olympiad_id = olympiads_col.insert_one(olympiad).inserted_id
        return jsonify({
            'message': 'Olympiad created successfully',
            'olympiad_id': str(olympiad_id)
        }), 201

    except Exception as e:
        logger.error(f"Error in create_olympiad: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/olympiads/<olympiad_id>', methods=['PUT'])
@token_required
@admin_required
def update_olympiad(current_user, olympiad_id):
    try:
        data = request.get_json()
        updates = {}
        if 'name' in data:
            updates['name'] = data['name']
        if 'description' in data:
            updates['description'] = data['description']
        if 'start_time' in data:
            updates['start_time'] = datetime.fromisoformat(data['start_time'])
        if 'end_time' in data:
            updates['end_time'] = datetime.fromisoformat(data['end_time'])
        if 'problems' in data:
            updates['problems'] = data['problems']

        result = olympiads_col.update_one(
            {'_id': ObjectId(olympiad_id)},
            {'$set': updates}
        )
        if result.modified_count == 0:
            return jsonify({'error': 'Olympiad not found or no changes made'}), 404
        return jsonify({'message': 'Olympiad updated successfully'})

    except Exception as e:
        logger.error(f"Error in update_olympiad: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/olympiads/<olympiad_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_olympiad(current_user, olympiad_id):
    try:
        result = olympiads_col.delete_one({'_id': ObjectId(olympiad_id)})
        if result.deleted_count == 0:
            return jsonify({'error': 'Olympiad not found'}), 404
        return jsonify({'message': 'Olympiad deleted successfully'})
    except Exception as e:
        logger.error(f"Error in delete_olympiad: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ====================== OLYMPIAD PARTICIPATION ======================
@app.route('/api/olympiads/<olympiad_id>/register', methods=['POST'])
@token_required
def register_for_olympiad(current_user, olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        existing = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        if existing:
            return jsonify({'error': 'Already registered for this olympiad'}), 400

        registration = {
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id']),
            'registered_at': datetime.utcnow(),
            'problems_solved': [],
            'total_points': 0
        }
        olympiad_participants_col.insert_one(registration)
        return jsonify({'message': 'Registered for olympiad successfully'}), 201

    except Exception as e:
        logger.error(f"Error in register_for_olympiad: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/submit', methods=['POST'])
@token_required
def submit_olympiad_solution(current_user, olympiad_id):
    try:
        data = request.get_json()
        required_fields = ['problem_id', 'code', 'language']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        participation = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        if not participation:
            return jsonify({'error': 'Not registered for this olympiad'}), 403

        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        now = datetime.now(timezone.utc)
        start_time = olympiad['start_time'].replace(tzinfo=timezone.utc)
        end_time = olympiad['end_time'].replace(tzinfo=timezone.utc)
        if now < start_time:
            return jsonify({'error': 'Olympiad has not started yet'}), 400
        if now > end_time:
            return jsonify({'error': 'Olympiad has ended'}), 400

        if data['problem_id'] not in olympiad['problems']:
            return jsonify({'error': 'Problem not part of this olympiad'}), 400

        if any(p['problem_id'] == data['problem_id'] for p in participation.get('problems_solved', [])):
            return jsonify({'error': 'Problem already solved'}), 400

        problem = problems_col.find_one({'id': data['problem_id']})
        if not problem:
            return jsonify({'error': 'Problem not found'}), 404

        result = process_submission(
            code=data['code'],
            language=data['language'],
            problem=problem,
            user_id=str(current_user['_id']),
            is_olympiad=True,
            olympiad_id=olympiad_id,
            participation=participation
        )
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in submit_olympiad_solution: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ====================== LEADERBOARD ======================
@app.route('/api/olympiads/<olympiad_id>/leaderboard', methods=['GET'])
def get_olympiad_leaderboard(olympiad_id):
    try:
        participants = list(olympiad_participants_col.find(
            {'olympiad_id': ObjectId(olympiad_id)},
            {'_id': 0, 'user_id': 1, 'total_points': 1, 'problems_solved': 1}
        ).sort('total_points', -1))

        for p in participants:
            user = users_col.find_one(
                {'_id': ObjectId(p['user_id'])},
                {'username': 1, '_id': 0}
            )
            p['username'] = user['username'] if user else 'Unknown'
            p['problems_solved_count'] = len(p['problems_solved'])

        return jsonify(participants)
    except Exception as e:
        logger.error(f"Error in get_olympiad_leaderboard: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ====================== ADMIN USER MANAGEMENT ======================
@app.route('/api/admin/users', methods=['GET'])
@token_required
@admin_required
def get_all_users(current_user):
    try:
        users = list(users_col.find({}, {'password': 0}))
        for user in users:
            user['_id'] = str(user['_id'])
        return jsonify(users)
    except Exception as e:
        logger.error(f"Error in get_all_users: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/users/<user_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_user(current_user, user_id):
    try:
        result = users_col.delete_one({'_id': ObjectId(user_id)})
        if result.deleted_count == 0:
            return jsonify({'error': 'User not found'}), 404
        return jsonify({'message': 'User deleted successfully'})
    except Exception as e:
        logger.error(f"Error in delete_user: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ====================== USER AUTHENTICATION ======================
@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({'error': 'Username and password are required'}), 400

        if users_col.find_one({'username': data['username']}):
            return jsonify({'error': 'Username already exists'}), 400

        hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
        user = {
            'username': data['username'],
            'password': hashed_password,
            'email': data.get('email', ''),
            'role': 'user',
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
            'solved_problems': [],
            'total_points': 0
        }
        user_id = users_col.insert_one(user).inserted_id
        token = jwt.encode({
            'user_id': str(user_id),
            'exp': datetime.utcnow() + timedelta(days=30)
        }, app.config['SECRET_KEY'], algorithm='HS256')
        return jsonify({
            'message': 'User registered successfully',
            'token': token,
            'user_id': str(user_id),
            'username': user['username']
        }), 201

    except Exception as e:
        logger.error(f"Error in register: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({'error': 'Username and password are required'}), 400

        user = users_col.find_one({'username': data['username']})
        if not user or not bcrypt.check_password_hash(user['password'], data['password']):
            return jsonify({'error': 'Invalid credentials'}), 401

        token = jwt.encode({
            'user_id': str(user['_id']),
            'exp': datetime.utcnow() + timedelta(days=30)
        }, app.config['SECRET_KEY'], algorithm='HS256')
        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user_id': str(user['_id']),
            'username': user['username'],
            'role': user.get('role', 'user')
        })

    except Exception as e:
        logger.error(f"Error in login: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/me', methods=['GET'])
@token_required
def get_current_user(current_user):
    try:
        user_data = {
            'user_id': str(current_user['_id']),
            'username': current_user['username'],
            'email': current_user.get('email', ''),
            'role': current_user.get('role', 'user'),
            'created_at': current_user['created_at'].strftime('%Y-%m-%d'),
            'solved_problems_count': len(current_user.get('solved_problems', [])),
            'total_points': current_user.get('total_points', 0)
        }
        return jsonify(user_data)
    except Exception as e:
        logger.error(f"Error in get_current_user: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ====================== ERROR HANDLERS ======================
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(400)
def bad_request(error):
    return jsonify({'error': 'Bad request'}), 400

@app.errorhandler(500)
def server_error(error):
    logger.error(f"Server error: {str(error)}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5055, debug=True)