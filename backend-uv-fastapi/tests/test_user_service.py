from app.schemas.user_schemas import UserResponse

def test_user_response_model_validate():
    u = type('FakeUser', (), {'id': 1, 'user_account': 'alice', 'user_name': None, 'user_avatar': None, 'user_profile': None, 'user_role': 'user', 'create_time': None})()
    print(UserResponse.model_validate(u))