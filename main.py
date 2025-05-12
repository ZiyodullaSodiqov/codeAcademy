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


# Load configurations
load_dotenv('.env')

app = Flask(__name__)

CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:3000"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
app.config["MONGO_URI"] = os.getenv("MONGO_URI" , "mongodb+srv://Ziyodulla:Ziyodulla0105@cluster0.vfh7g.mongodb.net/onlinejudge?retryWrites=true&w=majority&")
print(os.getenv("MONGO_URI"))
mongo = PyMongo(app)
bcrypt = Bcrypt(app)

# Language configurations
LANGUAGE_CONFIG = {
    'python': {
        'extension': '.py',
        'command': 'python3',
        'compile': None,
        'run': lambda filename: [filename]
    },
    'java': {
        'extension': '.java',
        'command': 'java',
        'compile': lambda filename: ['javac', filename],
        'run': lambda classname: ['java', '-cp', os.path.dirname(classname), os.path.basename(classname).replace('.java', '')]
    },
    'cpp': {
        'extension': '.cpp',
        'command': 'g++',
        'compile': lambda filename: ['g++', filename, '-o', filename.replace('.cpp', '')],
        'run': lambda filename: [filename.replace('.cpp', '')]
    },
    'javascript': {
        'extension': '.js',
        'command': 'node',
        'compile': None,
        'run': lambda filename: ['node', filename]
    }
}

try:
    problems_col = mongo.db.problems
    submissions_col = mongo.db.submissions
    users_col = mongo.db.users
    olympiads_col = mongo.db.olympiads
    olympiad_participants_col = mongo.db.olympiad_participantsmongo = PyMongo(app)
    mongo.cx.server_info()  # Test connection
    print("✅ MongoDB connected successfully!")
    
    # Initialize collections
    problems_col = mongo.db.problems
    submissions_col = mongo.db.submissions
    users_col = mongo.db.users
    olympiads_col = mongo.db.olympiads
    olympiad_participants_col = mongo.db.olympiad_participants
    print("✅ Collections initialized successfully")
except AttributeError as e:
    print(f"❌ FATAL: MongoDB connection failed: {str(e)}")
    # Handle this appropriately - maybe exit if DB is critical
    import sys
    sys.exit(1)

bcrypt = Bcrypt(app)
# Collections
problems_col = mongo.db.problems
submissions_col = mongo.db.submissions
users_col = mongo.db.users
olympiads_col = mongo.db.olympiads
olympiad_participants_col = mongo.db.olympiad_participants

# ====================== AUTH MIDDLEWARE ======================
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        if 'Authorization' in request.headers:
            token = request.headers['Authorization'].split(" ")[1]
            
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
            
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = users_col.find_one({'_id': ObjectId(data['user_id'])})
            if not current_user:
                return jsonify({'error': 'User not found'}), 404
            kwargs['current_user'] = current_user
        except Exception as e:
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
def execute_code(code, language, input_data, time_limit):
    """Execute code in the specified language with given input"""
    lang_config = LANGUAGE_CONFIG.get(language.lower())
    if not lang_config:
        raise ValueError(f"Unsupported language: {language}")

    temp_dir = tempfile.mkdtemp()
    try:
        # Create source file
        filename = os.path.join(temp_dir, f"source{lang_config['extension']}")
        with open(filename, 'w') as f:
            f.write(code)

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
                'status': 'Time Limit Exceeded',
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
        
# ====================== PROBLEM ENDPOINTS ======================
@app.route('/api/problems/<problem_id>/submit', methods=['POST'])
@token_required
def submit_problem_solution(current_user, problem_id):
    try:
        data = request.get_json()
        
        required_fields = ['code', 'language']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        # Find the problem
        problem = problems_col.find_one({'id': problem_id})
        if not problem:
            return jsonify({'error': 'Problem not found'}), 404

        # Create new submission
        submission = {
            'problem_id': problem_id,
            'user_id': ObjectId(current_user['_id']),
            'code': data['code'],
            'language': data['language'].lower(),
            'submitted_at': datetime.utcnow(),
            'status': 'Pending',
            'results': []
        }

        # Save submission to database
        submission_id = submissions_col.insert_one(submission).inserted_id

        test_results = []
        is_correct = True
        runtime = 0

        # Execute against each test case
        for test_case in problem.get('test_cases', []):
            try:
                result = execute_code(
                    code=data['code'],
                    language=data['language'],
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

                runtime = max(runtime, result.get('runtime', 0))
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

        # Update submission status
        final_status = 'Accepted' if is_correct else 'Rejected'
        submissions_col.update_one(
            {'_id': submission_id},
            {'$set': {
                'status': final_status,
                'results': test_results,
                'runtime': runtime
            }}
        )

        # Update user stats if correct
        if is_correct:
            users_col.update_one(
                {'_id': ObjectId(current_user['_id'])},
                {
                    '$addToSet': {'solved_problems': problem_id},
                    '$inc': {'total_points': 10}
                }
            )
            problems_col.update_one(
                {'id': problem_id},
                {'$inc': {'solved_count': 1}}
            )

        return jsonify({
            'submission_id': str(submission_id),
            'problem_id': problem_id,
            'status': final_status,
            'results': test_results,
            'runtime': runtime,
            'is_correct': is_correct
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
    
@app.route('/api/problems/<problem_id>', methods=['GET'])
def get_problem(problem_id):
    try:
        # ID bo'yicha masalani topish
        problem = problems_col.find_one({'id': problem_id}, {'_id': 0})
        
        if not problem:
            return jsonify({'error': 'Problem not found'}), 404
            
        return jsonify(problem)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/problems', methods=['GET'])
def get_all_problems():
    try:
        problems = list(problems_col.find({}, {'_id': 0}))
        return jsonify(problems)
    except Exception as e:
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
        return jsonify({'error': str(e)}), 500

# ====================== OLYMPIAD ENDPOINTS ======================
@app.route('/api/olympiads/<olympiad_id>', methods=['GET'])
def get_olympiad(olympiad_id):
    try:
        # Find olympiad by ID
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404
            
        # Convert ObjectId to string and format datetime fields
        olympiad['_id'] = str(olympiad['_id'])
        olympiad['created_by'] = str(olympiad['created_by'])
        olympiad['start_time'] = olympiad['start_time'].isoformat()
        olympiad['end_time'] = olympiad['end_time'].isoformat()
        olympiad['created_at'] = olympiad['created_at'].isoformat()
        
        return jsonify(olympiad)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/problems', methods=['GET'])
def get_olympiad_problems(olympiad_id):
    try:
        # First verify the olympiad exists
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        # Get all problems that are part of this olympiad
        problems = list(problems_col.find(
            {'id': {'$in': olympiad['problems']}},
            {'_id': 0}  # Exclude MongoDB _id field
        ))

        return jsonify(problems)

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/olympiads/<olympiad_id>/check-registration', methods=['GET'])
@token_required
def check_registration(current_user, olympiad_id):
    try:
        # Verify olympiad exists first
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        # Check registration status
        existing = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        
        return jsonify({
            'isRegistered': bool(existing),
            'olympiadStatus': olympiad.get('status', 'upcoming')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/olympiads', methods=['GET'])
def get_all_olympiads():
    try:
        olympiads = list(olympiads_col.find({}))
        
        # _id ni stringga aylantirish
        for olympiad in olympiads:
            olympiad['_id'] = str(olympiad['_id'])
            
        return jsonify(olympiads)
    except Exception as e:
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
        return jsonify({'error': str(e)}), 500

# ====================== OLYMPIAD PARTICIPATION ======================
@app.route('/api/olympiads/<olympiad_id>/register', methods=['POST'])
@token_required
def register_for_olympiad(current_user, olympiad_id):
    try:
        # Check if olympiad exists
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            return jsonify({'error': 'Olympiad not found'}), 404

        # Check if registration already exists
        existing = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        if existing:
            return jsonify({'error': 'Already registered for this olympiad'}), 400

        # Register participant
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
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/submit', methods=['POST'])
@token_required
def submit_olympiad_solution(current_user, olympiad_id):
    try:
        data = request.get_json()
        
        required_fields = ['problem_id', 'code', 'language']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        # Check user registration for olympiad
        participation = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        if not participation:
            return jsonify({'error': 'Not registered for this olympiad'}), 403

        # Check olympiad exists and timing
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

        # Check problem belongs to olympiad
        if data['problem_id'] not in olympiad['problems']:
            return jsonify({'error': 'Problem not part of this olympiad'}), 400

        # Check if problem already solved
        if any(p['problem_id'] == data['problem_id'] for p in participation.get('problems_solved', [])):
            return jsonify({'error': 'Problem already solved'}), 400

        # Get problem details
        problem = problems_col.find_one({'id': data['problem_id']})
        if not problem:
            return jsonify({'error': 'Problem not found'}), 404

        # Create submission record
        submission = {
            'problem_id': data['problem_id'],
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id']),
            'code': data['code'],
            'language': data['language'].lower(),
            'submitted_at': now,
            'status': 'Pending',
            'results': []
        }

        # Save submission
        submission_id = submissions_col.insert_one(submission).inserted_id

        test_results = []
        is_correct = True
        max_runtime = 0
        execution_start = datetime.now(timezone.utc)

        # Execute against each test case
        for test_case in problem.get('test_cases', []):
            try:
                result = execute_code(
                    code=data['code'],
                    language=data['language'],
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

        # Update submission status
        final_status = 'Accepted' if is_correct else 'Rejected'
        submissions_col.update_one(
            {'_id': submission_id},
            {'$set': {
                'status': final_status,
                'results': test_results,
                'runtime': max_runtime
            }}
        )

        # Calculate points if correct
        total_points = 0
        time_taken = (datetime.now(timezone.utc) - execution_start).total_seconds()

        if is_correct:
            # Base points based on difficulty
            base_points = {
                'easy': 100,
                'medium': 200,
                'hard': 300
            }.get(problem['difficulty'].lower(), 100)

            # Deduct 1 point per second until 10 points remain
            points_after_penalty = max(10, base_points - int(time_taken))
            total_points = points_after_penalty

            # Update participation
            update_data = {
                '$push': {
                    'problems_solved': {
                        'problem_id': data['problem_id'],
                        'solved_at': now,
                        'time_taken': time_taken,
                        'points_earned': total_points
                    }
                },
                '$inc': {'total_points': total_points}
            }

            olympiad_participants_col.update_one(
                {'_id': participation['_id']},
                update_data
            )

            # Update problem stats
            problems_col.update_one(
                {'id': data['problem_id']},
                {'$inc': {'solved_count': 1}}
            )

        return jsonify({
            'submission_id': str(submission_id),
            'status': final_status,
            'results': test_results,
            'points_earned': total_points if is_correct else 0,
            'time_taken': time_taken,
            'runtime': max_runtime,
            'is_correct': is_correct
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ====================== LEADERBOARD ======================
@app.route('/api/olympiads/<olympiad_id>/leaderboard', methods=['GET'])
def get_olympiad_leaderboard(olympiad_id):
    try:
        participants = list(olympiad_participants_col.find(
            {'olympiad_id': ObjectId(olympiad_id)},
            {'_id': 0, 'user_id': 1, 'total_points': 1, 'problems_solved': 1}
        ).sort('total_points', -1))

        # Add usernames
        for p in participants:
            user = users_col.find_one(
                {'_id': ObjectId(p['user_id'])},
                {'username': 1, '_id': 0}
            )
            p['username'] = user['username'] if user else 'Unknown'
            p['problems_solved_count'] = len(p['problems_solved'])

        return jsonify(participants)
    except Exception as e:
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
        return jsonify({'error': str(e)}), 500


# ====================== USER AUTHENTICATION ======================
@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        
        # Validation
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({'error': 'Username and password are required'}), 400
            
        # Check if username already exists
        if users_col.find_one({'username': data['username']}):
            return jsonify({'error': 'Username already exists'}), 400
            
        # Hash password
        hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
        
        # Create new user
        user = {
            'username': data['username'],
            'password': hashed_password,
            'email': data.get('email', ''),
            'role': 'user',  # Default role
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
            'solved_problems': [],
            'total_points': 0
        }
        
        # Insert user into database
        user_id = users_col.insert_one(user).inserted_id
        
        # Generate JWT token
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
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        
        # Validation
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({'error': 'Username and password are required'}), 400
            
        # Find user
        user = users_col.find_one({'username': data['username']})
        if not user:
            return jsonify({'error': 'Invalid credentials'}), 401
            
        # Check password
        if not bcrypt.check_password_hash(user['password'], data['password']):
            return jsonify({'error': 'Invalid credentials'}), 401
            
        # Generate JWT token
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
        return jsonify({'error': str(e)}), 500

@app.route('/api/me', methods=['GET'])
@token_required
def get_current_user(current_user):
    try:
        # Remove sensitive information before sending
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
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5055, debug=True)