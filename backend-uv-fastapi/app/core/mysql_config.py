# app/core/mysql_config.py
from app.core.settings_base import AppSettings

class MysqlSettings(AppSettings):
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