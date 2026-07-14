from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


DEPENDENCIES = ("torch", "transformers", "datasets", "peft", "trl", "bitsandbytes", "accelerate")


def load_records(path: str) -> list[dict[str, object]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            messages = item.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError(f"line {line_number}: messages list required")
            records.append(item)
    return records


def dependency_report() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in DEPENDENCIES}


def train(dataset_path: str, output_dir: str, model_id: str, epochs: float) -> None:
    import torch
    from datasets import Dataset
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    records = load_records(dataset_path)
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
    )
    tokenizer = AutoTokenizer.from_pretrained(model_id, use_fast=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=quantization,
        device_map="auto",
        dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
    )
    model = prepare_model_for_kbit_training(model)
    peft_config = LoraConfig(
        task_type="CAUSAL_LM", r=16, lora_alpha=32, lora_dropout=0.05,
        bias="none", target_modules="all-linear",
    )
    args = SFTConfig(
        output_dir=str(Path(output_dir).resolve()),
        num_train_epochs=float(epochs),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-4,
        logging_steps=5,
        save_strategy="epoch",
        save_total_limit=2,
        max_length=1024,
        gradient_checkpointing=True,
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),
        report_to="none",
        seed=42,
    )
    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=Dataset.from_list(records),
        peft_config=peft_config,
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(str(Path(output_dir).resolve()))
    tokenizer.save_pretrained(str(Path(output_dir).resolve()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Local QLoRA SFT for the opt-in ViPik conversation dataset")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", default="models/conversation-qwen3-8b-lora")
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--min-examples", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    records = load_records(args.dataset)
    dependencies = dependency_report()
    report = {"examples": len(records), "minimum": args.min_examples, "dependencies": dependencies}
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    missing = [name for name, available in dependencies.items() if not available]
    if missing:
        raise SystemExit("Missing optional training dependencies: " + ", ".join(missing))
    if len(records) < args.min_examples:
        raise SystemExit(f"Need at least {args.min_examples} approved examples; found {len(records)}")
    train(args.dataset, args.output_dir, args.model, args.epochs)


if __name__ == "__main__":
    main()
