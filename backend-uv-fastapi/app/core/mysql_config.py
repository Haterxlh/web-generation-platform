from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"

class MysqlSettings(BaseSettings):
    # extra="ignore" 表示：忽略 .env 里没有的字段，只保留 model_config 里的字段，
    # 如果 .env 里有字段，但是 model_config 里没有的字段，会报错。
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_db_name: str = "wgp_db"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db_name}?charset=utf8mb4"
        )


mysql_settings = MysqlSettings()

if __name__ == "__main__":
    print(f"Host: {mysql_settings.mysql_host}")
    print(f"Port: {mysql_settings.mysql_port}")
    print(f"DB Name: {mysql_settings.mysql_db_name}")
    # print(f"Database URL: {mysql_settings.database_url}")