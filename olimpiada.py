
from bson import ObjectId
from datetime import datetime
from flask_bcrypt import Bcrypt
import jwt
from functools import wraps
from flask import Blueprint, request, jsonify

olimpiada_bp = Blueprint('olimpiada', __name__, url_prefix='/api/olimpiada')

# Admin uchun token tekshirish dekoratori
# def admin_required(f):
#     @wraps(f)
#     def decorated(*args, **kwargs):
#         token = None
        
#         if 'Authorization' in request.headers:
#             token = request.headers['Authorization'].split(" ")[1]
            
#         if not token:
#             return jsonify({'error': 'Token is missing'}), 401
            
#         try:
#             data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
#             current_user = users_col.find_one({'_id': ObjectId(data['user_id']})
            
#             if current_user.get('role') != 'admin':
#                 return jsonify({'error': 'Admin privileges required'}), 403
                
#         except Exception as e:
#             return jsonify({'error': 'Token is invalid'}), 401
            
#         return f(current_user, *args, **kwargs)
        
#     return decorated

# # Olimpiada yaratish (Admin uchun)
# @olimpiada_bp.route('/', methods=['POST'])
# @admin_required
# def create_olimpiada():
#     try:
#         data = request.get_json()
        
#         required_fields = ['name', 'title', 'start_time', 'end_time', 'type', 'problems']
#         if not all(field in data for field in required_fields):
#             return jsonify({'error': 'Missing required fields'}), 400
        
#         # Vaqt formatini tekshirish
#         try:
#             start_time = datetime.fromisoformat(data['start_time'])
#             end_time = datetime.fromisoformat(data['end_time'])
#         except ValueError:
#             return jsonify({'error': 'Invalid time format. Use ISO format'}), 400
            
#         if start_time >= end_time:
#             return jsonify({'error': 'End time must be after start time'}), 400
            
#         # Olimpiada yaratish
#         olimpiada = {
#             'name': data['name'],
#             'title': data['title'],
#             'start_time': start_time,
#             'end_time': end_time,
#             'type': data['type'],
#             'problems': [ObjectId(problem_id) for problem_id in data['problems']],
#             'created_at': datetime.utcnow(),
#             'status': 'upcoming',
#             'participants': []
#         }
        
#         olimpiada_id = mongo.db.olimpiadas.insert_one(olimpiada).inserted_id
        
#         return jsonify({
#             'message': 'Olimpiada created successfully',
#             'olimpiada_id': str(olimpiada_id)
#         }), 201
        
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500

# # Olimpiadaga ro'yxatdan o'tish
# @olimpiada_bp.route('/<olimpiada_id>/register', methods=['POST'])
# def register_for_olimpiada(olimpiada_id):
#     try:
#         data = request.get_json()
        
#         # Olimpiadani tekshirish
#         olimpiada = mongo.db.olimpiadas.find_one({'_id': ObjectId(olimpiada_id)})
#         if not olimpiada:
#             return jsonify({'error': 'Olimpiada not found'}), 404
            
#         # Olimpiada boshlangan yoki tugagan bo'lsa
#         current_time = datetime.utcnow()
#         if current_time > olimpiada['end_time']:
#             return jsonify({'error': 'Olimpiada already ended'}), 400
#         elif current_time > olimpiada['start_time']:
#             return jsonify({'error': 'Olimpiada has already started'}), 400
            
#         # Talaba yoki o'quvchi ma'lumotlarini tekshirish
#         if olimpiada['type'] == 'universities':
#             required_fields = ['name', 'surname', 'phone', 'university', 'course', 'region']
#         else:  # maktablar
#             required_fields = ['name', 'surname', 'phone', 'school_number', 'school_name', 'class', 'region']
            
#         if not all(field in data for field in required_fields):
#             return jsonify({'error': 'Missing required fields'}), 400
            
#         # Telefon raqamini tekshirish
#         if not re.match(r'^\+?[0-9]{9,15}$', data['phone']):
#             return jsonify({'error': 'Invalid phone number'}), 400
            
#         # Parol yaratish (telefon raqamining oxirgi 4 raqami)
#         password = data['phone'][-4:]
        
#         # Foydalanuvchi yaratish
#         user_data = {
#             'name': data['name'],
#             'surname': data['surname'],
#             'phone': data['phone'],
#             'password': bcrypt.generate_password_hash(password).decode('utf-8'),
#             'role': 'participant',
#             'created_at': datetime.utcnow(),
#             'olimpiada_id': ObjectId(olimpiada_id),
#             'olimpiada_type': olimpiada['type']
#         }
        
#         # Turi bo'yicha qo'shimcha maydonlar
#         if olimpiada['type'] == 'universities':
#             user_data.update({
#                 'university': data['university'],
#                 'course': data['course']
#             })
#         else:
#             user_data.update({
#                 'school_number': data['school_number'],
#                 'school_name': data['school_name'],
#                 'class': data['class']
#             })
        
#         # Foydalanuvchini ro'yxatdan o'tkazish
#         user_id = mongo.db.users.insert_one(user_data).inserted_id
        
#         # Olimpiadaga qo'shish
#         mongo.db.olimpiadas.update_one(
#             {'_id': ObjectId(olimpiada_id)},
#             {'$push': {'participants': ObjectId(user_id)}}
#         )
        
#         return jsonify({
#             'message': 'Registered successfully',
#             'user_id': str(user_id),
#             'phone': data['phone'],
#             'password': password  # Faqat demo uchun, aslida buni yubormaslik kerak
#         }), 201
        
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500