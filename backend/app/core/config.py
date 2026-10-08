from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = "development"
    app_debug: bool = True
    database_url: str = "sqlite:///./database/app.db"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    demo_data: bool = False
    
    # Architecture Core Models (HNX26EPS05 System Architecture)
    embedding_model: str = "google/siglip-base-patch16-224"  # SigLIP (primary) or openai/clip-vit-base-patch16 (CLIP)
    yolo_model: str = "yolov8s-world.pt"                    # YOLO-World open-vocabulary detector
    frame_sampling_fps: float = 1.0                         # Frame sampling rate (1 FPS default)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
