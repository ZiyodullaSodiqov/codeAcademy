from pymongo import MongoClient

uri = "mongodb+srv://Ziyodulla:Ziyodulla0105@cluster0.vfh7g.mongodb.net/onlinejudge?retryWrites=true&w=majority"
client = MongoClient(uri)

try:
    client.server_info()
    print("Connected successfully!")
except Exception as e:
    print(f"Connection failed: {str(e)}")