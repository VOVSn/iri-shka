import torch
from diffusers import StableDiffusionPipeline, DPMSolverSinglestepScheduler
from PIL import Image
import os
import time # To measure generation time

# +++ ADD THIS LINE AT THE VERY TOP OF YOUR SCRIPT +++
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
# +++++++++++++++++++++++++++++++++++++++++++++++++++++

# --- Configuration ---
MODEL_FILENAME = "x.safetensors"  # Your model filename
MODEL_PATH = os.path.join("models", MODEL_FILENAME)
OUTPUT_FILENAME = "img/hyper_generated_image.png"

PROMPT = "alien sittin on shiny bike"
NEGATIVE_PROMPT = "ugly, blurry, noisy, low quality, text, watermark, signature, deformed, monochrome"

# Hyper parameters
NUM_INFERENCE_STEPS = 6
CFG_SCALE = 2.0
SAMPLER_NAME = "DPM++ SDE Karras"

WIDTH = 512
HEIGHT = 512
SEED = None

def main():
    if not os.path.exists(MODEL_PATH):
        print(f"Error: Model file not found at {MODEL_PATH}")
        print(f"Please make sure '{MODEL_FILENAME}' is in the 'models' directory.")
        return

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch_dtype = torch.float16 if device == "cuda" else torch.float32

    print(f"Loading model: {MODEL_PATH} to {device} with dtype {torch_dtype}...")
    start_load_time = time.time()
    try:
        pipe = StableDiffusionPipeline.from_single_file(
            MODEL_PATH,
            torch_dtype=torch_dtype,
            safety_checker=None,
            requires_safety_checker=False,
            load_safety_checker=False,
            # It's good practice to specify the original config for SD1.5 based .safetensors
            #original_config_file="https://huggingface.co/runwayml/stable-diffusion-v1-5/raw/main/v1-inference.yaml",
            # For float16, upcast_attention can improve quality/stability for some SD1.5 models
            upcast_attention=(True if torch_dtype == torch.float16 else False),
            # If after the first successful run (with symlinks disabled) you want to ensure no new downloads:
            # local_files_only=True, # Use cautiously, ensure base components are fully cached first
        )
    except Exception as e:
        print(f"Error loading model: {e}")
        # ... (rest of your error handling)
        return

    pipe = pipe.to(device)
    #pipe.safety_checker = lambda images, **kwargs: (images, False)
    load_time = time.time() - start_load_time
    print(f"Model loaded in {load_time:.2f} seconds.")

    if SAMPLER_NAME == "DPM++ SDE Karras":
        print(f"Setting sampler to {SAMPLER_NAME}...")
        pipe.scheduler = DPMSolverSinglestepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
    else:
        print(f"Warning: Sampler '{SAMPLER_NAME}' not explicitly configured.")

    if device == "cuda":
        print("Enabling attention slicing for lower VRAM usage.")
        try:
            pipe.enable_attention_slicing()
        except Exception as e:
            print(f"Could not enable attention slicing (this is often fine): {e}")

    generator = torch.Generator(device=device)
    if SEED is not None:
        generator = generator.manual_seed(SEED)
        print(f"Using seed: {SEED}")
    else:
        print("Using random seed.")

    print(f"\nGenerating image with prompt: \"{PROMPT}\"")
    print(f"Steps: {NUM_INFERENCE_STEPS}, CFG Scale: {CFG_SCALE}, Sampler: {pipe.scheduler.__class__.__name__}")

    start_gen_time = time.time()
    with torch.no_grad():
        try:
            result = pipe(
                prompt=PROMPT,
                negative_prompt=NEGATIVE_PROMPT,
                num_inference_steps=NUM_INFERENCE_STEPS,
                guidance_scale=CFG_SCALE,
                width=WIDTH,
                height=HEIGHT,
                generator=generator
            )
            image = result.images[0]
        except Exception as e:
            print(f"Error during image generation: {e}")
            if "out of memory" in str(e).lower() and device == "cuda":
                print("CUDA out of memory. Try reducing width/height, or if you have very low VRAM,")
                print("consider enabling CPU offloading (pipe.enable_sequential_cpu_offload()) after loading the model, though it will be slower.")
            return

    gen_time = time.time() - start_gen_time
    print(f"Image generated in {gen_time:.2f} seconds.")

    try:
        image.save(OUTPUT_FILENAME)
        print(f"Image saved to {os.path.abspath(OUTPUT_FILENAME)}")
    except Exception as e:
        print(f"Error saving image: {e}")

    del pipe
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print("\nTest complete.")

if __name__ == "__main__":
    main()