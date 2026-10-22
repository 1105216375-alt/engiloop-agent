"""Run separate synthetic mechanism acceptance demos, not one field assessment."""
import runpy


MODULES = (
    "semantic_intake", "report_intake", "residual_reasoning",
    "reverse_verification", "reviewed_memory", "engineering_scoring",
    "checkpoints_and_rollback", "native_trace",
)


def main():
    for name in MODULES:
        print(f"\n===== SYNTHETIC MECHANISM: {name} =====", flush=True)
        runpy.run_module(f"examples.{name}", run_name="__main__")
    print("\nAll synthetic mechanism demos completed; not a field assessment.")


if __name__ == "__main__":
    main()
