import os
import logging
import tempfile
import subprocess
import shutil
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from uuid import uuid4
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_pymongo import PyMongo
from flask_cors import CORS
from flask_bcrypt import Bcrypt
import jwt
from bson import ObjectId

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configure CORS
CORS_ORIGINS = os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')
CORS(app, resources={
    r"/api/*": {
        "origins": CORS_ORIGINS,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

# Flask configurations
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['MONGO_URI'] = os.getenv('MONGO_URI')

# Validate critical environment variables
if not app.config['SECRET_KEY']:
    logger.error("SECRET_KEY is not set in environment variables")
    raise ValueError("SECRET_KEY is required")
if not app.config['MONGO_URI']:
    logger.error("MONGO_URI is not set in environment variables")
    raise ValueError("MONGO_URI is required")

# Initialize extensions
mongo = PyMongo(app)
bcrypt = Bcrypt(app)

# Language configurations with adjusted time limits
LANGUAGE_CONFIG = {
    'python': {
        'extension': '.py',
        'command': 'python3',
        'compile': None,
        'run': lambda filename: ['python3', filename],
        'time_limit': 2.0
    },
    'java': {
        'extension': '.java',
        'command': 'java',
        'compile': lambda filename: ['javac', filename],
        'run': lambda classname: ['java', '-cp', os.path.dirname(classname), os.path.basename(classname).replace('.java', '')],
        'time_limit': 4.0
    },
    'cpp': {
        'extension': '.cpp',
        'command': 'g++',
        'compile': lambda filename: ['g++', filename, '-o', filename.replace('.cpp', '')],
        'run': lambda filename: [filename.replace('.cpp', '')],
        'time_limit': 3.0
    },
    'javascript': {
        'extension': '.js',
        'command': 'node',
        'compile': None,
        'run': lambda filename: ['node', filename],
        'time_limit': 2.5
    }
}

# Initialize MongoDB collections
try:
    mongo.cx.server_info()
    logger.info("MongoDB connected successfully")
    problems_col = mongo.db.problems
    submissions_col = mongo.db.submissions
    users_col = mongo.db.users
    olympiads_col = mongo.db.olympiads
    olympiad_participants_col = mongo.db.olympiad_participants
    logger.info("Collections initialized successfully")
except Exception as e:
    logger.error(f"Fatal: MongoDB connection failed: {str(e)}")
    raise SystemExit(1)

# Middleware for token validation
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
        except Exception as e:
            logger.error(f"Token validation error: {str(e)}")
            return jsonify({'error': 'Token processing error'}), 401

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
    """Execute code in a specified language with given input, using restricted permissions"""
    lang_config = LANGUAGE_CONFIG.get(language.lower())
    if not lang_config:
        logger.error(f"Unsupported language: {language}")
        raise ValueError(f"Unsupported language: {language}")

    temp_dir = tempfile.mkdtemp()
    execution_id = str(uuid4())
    logger.info(f"Execution {execution_id}: Starting for language {language}")

    try:
        # Create source file
        filename = os.path.join(temp_dir, f"source{lang_config['extension']}")
        with open(filename, 'w') as f:
            f.write(code)
        os.chmod(filename, 0o644)

        # Create input file for JavaScript
        input_file = None
        if language.lower() == 'javascript':
            input_file = os.path.join(temp_dir, 'input.txt')
            with open(input_file, 'w') as f:
                f.write(input_data)

        # Compile if needed
        if lang_config['compile']:
            compile_proc = subprocess.run(
                lang_config['compile'](filename),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=lang_config['time_limit'],
                cwd=temp_dir
            )
            if compile_proc.returncode != 0:
                logger.warning(f"Execution {execution_id}: Compilation error")
                return {
                    'status': 'Compilation Error',
                    'error': compile_proc.stderr,
                    'runtime': 0
                }

        # Prepare run command
        run_command = lang_config['run'](filename)
        if language.lower() == 'javascript' and input_file:
            run_command = ['node', filename, '<', input_file]

        # Run the program with restricted permissions
        start_time = time.time()
        process = subprocess.Popen(
            run_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=temp_dir,
            shell=(language.lower() == 'javascript')  # Shell needed for input redirection
        )

        try:
            stdout, stderr = process.communicate(
                input=input_data if language.lower() != 'javascript' else None,
                timeout=lang_config['time_limit']
            )
            runtime = time.time() - start_time

            if process.returncode != 0:
                logger.warning(f"Execution {execution_id}: Runtime error")
                return {
                    'status': 'Runtime Error',
                    'output': stdout,
                    'error': stderr,
                    'runtime': runtime
                }

            logger.info(f"Execution {execution_id}: Completed successfully")
            return {
                'status': 'Accepted',
                'output': stdout.strip(),
                'runtime': runtime
            }

        except subprocess.TimeoutExpired:
            process.kill()
            logger.warning(f"Execution {execution_id}: Time Limit Exceeded")
            return {
                'status': 'Time Limit Exceeded',
                'runtime': lang_config['time_limit']
            }

    except Exception as e:
        logger.error(f"Execution {execution_id}: Evaluation error: {str(e)}")
        return {
            'status': 'Evaluation Error',
            'error': str(e),
            'runtime': 0
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# Problem Endpoints
@app.route('/api/problems/<problem_id>/submit', methods=['POST'])
@token_required
def submit_problem_solution(current_user, problem_id):
    try:
        data = request.get_json()
        required_fields = ['code', 'language']
        if not all(field in data for field in required_fields):
            logger.warning(f"Submission by {current_user['username']}: Missing required fields")
            return jsonify({'error': 'Missing required fields'}), 400

        if data['language'].lower() not in LANGUAGE_CONFIG:
            logger.warning(f"Submission by {current_user['username']}: Invalid language {data['language']}")
            return jsonify({'error': 'Unsupported language'}), 400

        problem = problems_col.find_one({'id': problem_id})
        if not problem:
            logger.warning(f"Submission by {current_user['username']}: Problem {problem_id} not found")
            return jsonify({'error': 'Problem not found'}), 404

        submission = {
            'problem_id': problem_id,
            'user_id': ObjectId(current_user['_id']),
            'code': data['code'],
            'language': data['language'].lower(),
            'submitted_at': datetime.utcnow(),
            'status': 'Pending',
            'results': []
        }

        submission_id = submissions_col.insert_one(submission).inserted_id
        logger.info(f"Submission {submission_id}: Created for problem {problem_id}")

        test_results = []
        is_correct = True
        runtime = 0

        for test_case in problem.get('test_cases', []):
            try:
                result = execute_code(
                    code=data['code'],
                    language=data['language'],
                    input_data=test_case['input'],
                    time_limit=problem.get('time_limit', LANGUAGE_CONFIG[data['language'].lower()]['time_limit'])
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
                logger.error(f"Submission {submission_id}: Test case error: {str(e)}")
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
                'runtime': runtime
            }}
        )

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
            logger.info(f"Submission {submission_id}: Accepted, user stats updated")

        return jsonify({
            'submission_id': str(submission_id),
            'problem_id': problem_id,
            'status': final_status,
            'results': test_results,
            'runtime': runtime,
            'is_correct': is_correct
        })

    except Exception as e:
        logger.error(f"Submission error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/problems/<problem_id>', methods=['GET'])
def get_problem(problem_id):
    try:
        problem = problems_col.find_one({'id': problem_id}, {'_id': 0})
        if not problem:
            logger.warning(f"Problem {problem_id} not found")
            return jsonify({'error': 'Problem not found'}), 404
        return jsonify(problem)
    except Exception as e:
        logger.error(f"Get problem error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/problems', methods=['GET'])
def get_all_problems():
    try:
        problems = list(problems_col.find({}, {'_id': 0}))
        return jsonify(problems)
    except Exception as e:
        logger.error(f"Get all problems error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/problems', methods=['POST'])
@token_required
@admin_required
def create_problem(current_user):
    try:
        data = request.get_json()
        required_fields = ['id', 'title', 'description', 'difficulty', 'test_cases']
        if not all(field in data for field in required_fields):
            logger.warning(f"Problem creation by {current_user['username']}: Missing required fields")
            return jsonify({'error': 'Missing required fields'}), 400

        if not all(isinstance(tc, dict) and 'input' in tc and 'output' in tc for tc in data['test_cases']):
            logger.warning(f"Problem creation by {current_user['username']}: Invalid test cases")
            return jsonify({'error': 'Invalid test case format'}), 400

        if problems_col.find_one({'id': data['id']}):
            logger.warning(f"Problem creation by {current_user['username']}: Problem ID {data['id']} exists")
            return jsonify({'error': 'Problem ID already exists'}), 400

        problem = {
            'id': data['id'],
            'title': data['title'],
            'description': data['description'],
            'difficulty': data['difficulty'].lower(),
            'tags': data.get('tags', []),
            'time_limit': data.get('time_limit', 2.0),
            'memory_limit': data.get('memory_limit', 256),
            'test_cases': data['test_cases'],
            'created_at': datetime.utcnow(),
            'created_by': str(current_user['_id']),
            'solved_count': 0
        }

        problems_col.insert_one(problem)
        logger.info(f"Problem {data['id']} created by {current_user['username']}")
        return jsonify({'message': 'Problem created successfully'}), 201

    except Exception as e:
        logger.error(f"Problem creation error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/problems/<problem_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_problem(current_user, problem_id):
    try:
        result = problems_col.delete_one({'id': problem_id})
        if result.deleted_count == 0:
            logger.warning(f"Problem deletion by {current_user['username']}: Problem {problem_id} not found")
            return jsonify({'error': 'Problem not found'}), 404
        logger.info(f"Problem {problem_id} deleted by {current_user['username']}")
        return jsonify({'message': 'Problem deleted successfully'})
    except Exception as e:
        logger.error(f"Problem deletion error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Olympiad Endpoints
@app.route('/api/olympiads/<olympiad_id>', methods=['GET'])
def get_olympiad(olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            logger.warning(f"Olympiad {olympiad_id} not found")
            return jsonify({'error': 'Olympiad not found'}), 404

        olympiad['_id'] = str(olympiad['_id'])
        olympiad['created_by'] = str(olympiad['created_by'])
        olympiad['start_time'] = olympiad['start_time'].isoformat()
        olympiad['end_time'] = olympiad['end_time'].isoformat()
        olympiad['created_at'] = olympiad['created_at'].isoformat()

        return jsonify(olympiad)
    except Exception as e:
        logger.error(f"Get olympiad error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/problems', methods=['GET'])
def get_olympiad_problems(olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            logger.warning(f"Olympiad {olympiad_id} not found")
            return jsonify({'error': 'Olympiad not found'}), 404

        problems = list(problems_col.find(
            {'id': {'$in': olympiad['problems']}},
            {'_id': 0}
        ))
        return jsonify(problems)
    except Exception as e:
        logger.error(f"Get olympiad problems error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/check-registration', methods=['GET'])
@token_required
def check_registration(current_user, olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            logger.warning(f"Olympiad {olympiad_id} not found")
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
        logger.error(f"Check registration error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads', methods=['GET'])
def get_all_olympiads():
    try:
        olympiads = list(olympiads_col.find({}))
        for olympiad in olympiads:
            olympiad['_id'] = str(olympiad['_id'])
            olympiad['created_by'] = str(olympiad['created_by'])
            olympiad['start_time'] = olympiad['start_time'].isoformat()
            olympiad['end_time'] = olympiad['end_time'].isoformat()
            olympiad['created_at'] = olympiad['created_at'].isoformat()
        return jsonify(olympiads)
    except Exception as e:
        logger.error(f"Get all olympiads error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/olympiads', methods=['POST'])
@token_required
@admin_required
def create_olympiad(current_user):
    try:
        data = request.get_json()
        required_fields = ['name', 'start_time', 'end_time', 'problems']
        if not all(field in data for field in required_fields):
            logger.warning(f"Olympiad creation by {current_user['username']}: Missing required fields")
            return jsonify({'error': 'Missing required fields'}), 400

        try:
            start_time = datetime.fromisoformat(data['start_time'])
            end_time = datetime.fromisoformat(data['end_time'])
            if start_time >= end_time:
                logger.warning(f"Olympiad creation by {current_user['username']}: Invalid time range")
                return jsonify({'error': 'End time must be after start time'}), 400
        except ValueError:
            logger.warning(f"Olympiad creation by {current_user['username']}: Invalid datetime format")
            return jsonify({'error': 'Invalid datetime format'}), 400

        if not isinstance(data['problems'], list) or not data['problems']:
            logger.warning(f"Olympiad creation by {current_user['username']}: Invalid problems list")
            return jsonify({'error': 'Problems must be a non-empty list'}), 400

        olympiad = {
            'name': data['name'],
            'description': data.get('description', ''),
            'start_time': start_time,
            'end_time': end_time,
            'problems': data['problems'],
            'created_at': datetime.utcnow(),
            'created_by': str(current_user['_id']),
            'status': 'upcoming'
        }

        olympiad_id = olympiads_col.insert_one(olympiad).inserted_id
        logger.info(f"Olympiad {olympiad_id} created by {current_user['username']}")
        return jsonify({
            'message': 'Olympiad created successfully',
            'olympiad_id': str(olympiad_id)
        }), 201

    except Exception as e:
        logger.error(f"Olympiad creation error: {str(e)}")
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
            try:
                updates['start_time'] = datetime.fromisoformat(data['start_time'])
            except ValueError:
                logger.warning(f"Olympiad update by {current_user['username']}: Invalid start_time format")
                return jsonify({'error': 'Invalid start_time format'}), 400
        if 'end_time' in data:
            try:
                updates['end_time'] = datetime.fromisoformat(data['end_time'])
            except ValueError:
                logger.warning(f"Olympiad update by {current_user['username']}: Invalid end_time format")
                return jsonify({'error': 'Invalid end_time format'}), 400
        if 'problems' in data:
            if not isinstance(data['problems'], list):
                logger.warning(f"Olympiad update by {current_user['username']}: Invalid problems format")
                return jsonify({'error': 'Problems must be a list'}), 400
            updates['problems'] = data['problems']

        result = olympiads_col.update_one(
            {'_id': ObjectId(olympiad_id)},
            {'$set': updates}
        )

        if result.modified_count == 0:
            logger.warning(f"Olympiad update by {current_user['username']}: Olympiad {olympiad_id} not found or no changes")
            return jsonify({'error': 'Olympiad not found or no changes made'}), 404

        logger.info(f"Olympiad {olympiad_id} updated by {current_user['username']}")
        return jsonify({'message': 'Olympiad updated successfully'})

    except Exception as e:
        logger.error(f"Olympiad update error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/olympiads/<olympiad_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_olympiad(current_user, olympiad_id):
    try:
        result = olympiads_col.delete_one({'_id': ObjectId(olympiad_id)})
        if result.deleted_count == 0:
            logger.warning(f"Olympiad deletion by {current_user['username']}: Olympiad {olympiad_id} not found")
            return jsonify({'error': 'Olympiad not found'}), 404
        logger.info(f"Olympiad {olympiad_id} deleted by {current_user['username']}")
        return jsonify({'message': 'Olympiad deleted successfully'})
    except Exception as e:
        logger.error(f"Olympiad deletion error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Olympiad Participation
@app.route('/api/olympiads/<olympiad_id>/register', methods=['POST'])
@token_required
def register_for_olympiad(current_user, olympiad_id):
    try:
        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            logger.warning(f"Registration by {current_user['username']}: Olympiad {olympiad_id} not found")
            return jsonify({'error': 'Olympiad not found'}), 404

        existing = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        if existing:
            logger.warning(f"Registration by {current_user['username']}: Already registered for olympiad {olympiad_id}")
            return jsonify({'error': 'Already registered for this olympiad'}), 400

        registration = {
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id']),
            'registered_at': datetime.utcnow(),
            'problems_solved': [],
            'total_points': 0
        }

        olympiad_participants_col.insert_one(registration)
        logger.info(f"User {current_user['username']} registered for olympiad {olympiad_id}")
        return jsonify({'message': 'Registered for olympiad successfully'}), 201

    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/olympiads/<olympiad_id>/submit', methods=['POST'])
@token_required
def submit_olympiad_solution(current_user, olympiad_id):
    try:
        data = request.get_json()
        required_fields = ['problem_id', 'code', 'language']
        if not all(field in data for field in required_fields):
            logger.warning(f"Olympiad submission by {current_user['username']}: Missing required fields")
            return jsonify({'error': 'Missing required fields'}), 400

        if data['language'].lower() not in LANGUAGE_CONFIG:
            logger.warning(f"Olympiad submission by {current_user['username']}: Invalid language {data['language']}")
            return jsonify({'error': 'Unsupported language'}), 400

        participation = olympiad_participants_col.find_one({
            'olympiad_id': ObjectId(olympiad_id),
            'user_id': ObjectId(current_user['_id'])
        })
        if not participation:
            logger.warning(f"Olympiad submission by {current_user['username']}: Not registered for olympiad {olympiad_id}")
            return jsonify({'error': 'Not registered for this olympiad'}), 403

        olympiad = olympiads_col.find_one({'_id': ObjectId(olympiad_id)})
        if not olympiad:
            logger.warning(f"Olympiad submission by {current_user['username']}: Olympiad {olympiad_id} not found")
            return jsonify({'error': 'Olympiad not found'}), 404

        now = datetime.now(timezone.utc)
        start_time = olympiad['start_time'].replace(tzinfo=timezone.utc)
        end_time = olympiad['end_time'].replace(tzinfo=timezone.utc)

        if now < start_time:
            logger.warning(f"Olympiad submission by {current_user['username']}: Olympiad {olympiad_id} not started")
            return jsonify({'error': 'Olympiad has not started yet'}), 400
        if now > end_time:
            logger.warning(f"Olympiad submission by {current_user['username']}: Olympiad {olympiad_id} ended")
            return jsonify({'error': 'Olympiad has ended'}), 400

        if data['problem_id'] not in olympiad['problems']:
            logger.warning(f"Olympiad submission by {current_user['username']}: Problem {data['problem_id']} not in olympiad")
            return jsonify({'error': 'Problem not part of this olympiad'}), 400

        if any(p['problem_id'] == data['problem_id'] for p in participation.get('problems_solved', [])):
            logger.warning(f"Olympiad submission by {current_user['username']}: Problem {data['problem_id']} already solved")
            return jsonify({'error': 'Problem already solved'}), 400

        problem = problems_col.find_one({'id': data['problem_id']})
        if not problem:
            logger.warning(f"Olympiad submission by {current_user['username']}: Problem {data['problem_id']} not found")
            return jsonify({'error': 'Problem not found'}), 404

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

        submission_id = submissions_col.insert_one(submission).inserted_id
        logger.info(f"Olympiad submission {submission_id}: Created for problem {data['problem_id']}")

        test_results = []
        is_correct = True
        max_runtime = 0
        execution_start = datetime.now(timezone.utc)

        for test_case in problem.get('test_cases', []):
            try:
                result = execute_code(
                    code=data['code'],
                    language=data['language'],
                    input_data=test_case['input'],
                    time_limit=problem.get('time_limit', LANGUAGE_CONFIG[data['language'].lower()]['time_limit'])
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
                logger.error(f"Olympiad submission {submission_id}: Test case error: {str(e)}")
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

        total_points = 0
        time_taken = (datetime.now(timezone.utc) - execution_start).total_seconds()

        if is_correct:
            base_points = {
                'easy': 100,
                'medium': 200,
                'hard': 300
            }.get(problem['difficulty'].lower(), 100)

            points_after_penalty = max(10, base_points - int(time_taken))
            total_points = points_after_penalty

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

            problems_col.update_one(
                {'id': data['problem_id']},
                {'$inc': {'solved_count': 1}}
            )
            logger.info(f"Olympiad submission {submission_id}: Accepted, points awarded: {total_points}")

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
        logger.error(f"Olympiad submission error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Leaderboard
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
        logger.error(f"Leaderboard error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Admin User Management
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
        logger.error(f"Get all users error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/users/<user_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_user(current_user, user_id):
    try:
        result = users_col.delete_one({'_id': ObjectId(user_id)})
        if result.deleted_count == 0:
            logger.warning(f"User deletion by {current_user['username']}: User {user_id} not found")
            return jsonify({'error': 'User not found'}), 404
        logger.info(f"User {user_id} deleted by {current_user['username']}")
        return jsonify({'message': 'User deleted successfully'})
    except Exception as e:
        logger.error(f"User deletion error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# User Authentication
@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        if not data or 'username' not in data or 'password' not in data:
            logger.warning("Registration attempt: Missing username or password")
            return jsonify({'error': 'Username and password are required'}), 400

        if len(data['username']) < 3 or len(data['password']) < 6:
            logger.warning("Registration attempt: Invalid username or password length")
            return jsonify({'error': 'Username must be at least 3 characters and password at least 6 characters'}), 400

        if users_col.find_one({'username': data['username']}):
            logger.warning(f"Registration attempt: Username {data['username']} already exists")
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

        logger.info(f"User {data['username']} registered successfully")
        return jsonify({
            'message': 'User registered successfully',
            'token': token,
            'user_id': str(user_id),
            'username': user['username']
        }), 201

    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data or 'username' not in data or 'password' not in data:
            logger.warning("Login attempt: Missing username or password")
            return jsonify({'error': 'Username and password are required'}), 400

        user = users_col.find_one({'username': data['username']})
        if not user:
            logger.warning(f"Login attempt: Invalid username {data['username']}")
            return jsonify({'error': 'Invalid credentials'}), 401

        if not bcrypt.check_password_hash(user['password'], data['password']):
            logger.warning(f"Login attempt: Invalid password for {data['username']}")
            return jsonify({'error': 'Invalid credentials'}), 401

        token = jwt.encode({
            'user_id': str(user['_id']),
            'exp': datetime.utcnow() + timedelta(days=30)
        }, app.config['SECRET_KEY'], algorithm='HS256')

        logger.info(f"User {data['username']} logged in successfully")
        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user_id': str(user['_id']),
            'username': user['username'],
            'role': user.get('role', 'user')
        })

    except Exception as e:
        logger.error(f"Login error: {str(e)}")
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
        logger.error(f"Get current user error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Error Handlers
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
    # For development only; use Gunicorn for production
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5055)), debug=os.getenv('FLASK_ENV') == 'development')