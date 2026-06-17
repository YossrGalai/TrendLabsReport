from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://root:changeme@mysql:3306/trendlabs"
    jwt_secret: str = "changeme"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    trello_api_key: str = ""
    trello_token: str = ""
    anthropic_api_key: str = ""


settings = Settings()
