import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import torch
from PIL import Image
from backend.app.core.config import settings
from backend.app.core.hardware import detect_hardware
from backend.app.core.logging import logger

class EmbeddingModel:
    def __init__(self, model_name=None):
        if not model_name:
            model_name = getattr(settings, "embedding_model", os.environ.get("EMBEDDING_MODEL", "google/siglip-base-patch16-224"))
        
        self.model_name = model_name
        hw_info = detect_hardware()
        self.device = hw_info.get("primary_device", "cpu")
        self.dimension = 768 if "siglip" in model_name.lower() else 512
        
        logger.info(f"Loading Multimodal Embedding Model ({model_name}) on {self.device}...")
        try:
            from transformers import AutoProcessor, AutoModel
            try:
                self.model = AutoModel.from_pretrained(model_name, local_files_only=True)
                self.processor = AutoProcessor.from_pretrained(model_name, local_files_only=True)
            except Exception:
                self.model = AutoModel.from_pretrained(model_name)
                self.processor = AutoProcessor.from_pretrained(model_name)

            if self.device != "cpu":
                self.model.to(self.device)
            self.model.eval()
            self.available = True
            
            # Determine actual vector dimension from model config
            if hasattr(self.model, "config"):
                if hasattr(self.model.config, "projection_dim"):
                    self.dimension = self.model.config.projection_dim
                elif hasattr(self.model.config, "text_config") and hasattr(self.model.config.text_config, "hidden_size"):
                    self.dimension = self.model.config.text_config.hidden_size
            logger.info(f"Embedding model loaded successfully. Family: {'SigLIP' if 'siglip' in model_name.lower() else 'CLIP'} | Vector Dimension: {self.dimension}")
        except Exception as e:
            logger.error(f"Failed to load embedding model ({model_name}): {e}")
            self.available = False
            # Seamless fallback to alternate cached model (CLIP or SigLIP)
            fallback = "openai/clip-vit-base-patch16" if "siglip" in model_name.lower() else "google/siglip-base-patch16-224"
            logger.info(f"Attempting fallback to {fallback}...")
            try:
                from transformers import AutoProcessor, AutoModel
                self.model = AutoModel.from_pretrained(fallback, local_files_only=True)
                self.processor = AutoProcessor.from_pretrained(fallback, local_files_only=True)
                if self.device != "cpu":
                    self.model.to(self.device)
                self.model.eval()
                self.model_name = fallback
                self.dimension = 768 if "siglip" in fallback.lower() else 512
                self.available = True
                logger.info(f"Successfully loaded fallback embedding model ({fallback}).")
            except Exception as e2:
                logger.error(f"Fallback embedding model failed: {e2}")
                self.available = False

    @torch.no_grad()
    def embed_image(self, image_path: str):
        if not self.available: return None
        try:
            image = Image.open(image_path).convert("RGB")
            inputs = self.processor(images=image, return_tensors="pt").to(self.device)
            image_features = self.model.get_image_features(**inputs)
            # Normalize
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            return image_features.cpu().numpy()[0]
        except Exception as e:
            logger.error(f"Error embedding image {image_path}: {e}")
            return None

    @torch.no_grad()
    def embed_text(self, text: str):
        if not self.available: return None
        try:
            inputs = self.processor(text=[text], padding="max_length", return_tensors="pt").to(self.device)
            text_features = self.model.get_text_features(**inputs)
            # Normalize
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            return text_features.cpu().numpy()[0]
        except Exception as e:
            logger.error(f"Error embedding text '{text}': {e}")
            return None

import threading

_embedding_instance = None
_embedder_lock = threading.Lock()

def get_embedder(model_name: str = None):
    global _embedding_instance
    with _embedder_lock:
        if _embedding_instance is None or (model_name and _embedding_instance.model_name != model_name):
            _embedding_instance = EmbeddingModel(model_name)
        return _embedding_instance

