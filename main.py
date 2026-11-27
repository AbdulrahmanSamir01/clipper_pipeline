import sys
from pipeline_runner import run_pipeline

def main():
    # قائمة القنوات المفعلة تلقائياً
    channels = [
        "ar/asamirtech",
        "ar/al_qesa",
        # "ar/abdo_ai",
        # "ar/abdo_economy",
        ]

    # دعم اختيار قناة واحدة عبر ترويسة الأوامر (Command Line Argument)
    # مثال: python main.py al_qesa
    if len(sys.argv) > 1:
        channels = [sys.argv[1]]

    print(f"[*] Channels scheduled for execution: {channels}")

    for channel in channels:
        try:
            run_pipeline(channel)
        except Exception as e:
            print(f"[-] Critical error encountered while processing channel '{channel}': {e}")

if __name__ == "__main__":
    main()