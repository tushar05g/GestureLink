import sys
import webview
import os


def main():
    if len(sys.argv) < 2:
        print("Usage: python webview_launcher.py <url>")
        sys.exit(1)

    url = sys.argv[1]

    # Try to resolve an icon if we are running locally or built
    possible_icons = [
        "src/hub/static/icon.ico",
        "icon.ico",
        "../src/hub/static/icon.ico"
    ]
    for p in possible_icons:
        if os.path.exists(p):
            break

    # Create a native window
    window = webview.create_window(
        'GestureLink Hub',
        url,
        width=1100,
        height=750,
        min_size=(800, 600),
        background_color='#0f172a'  # Match our dark theme
    )

    # Start the native webview loop
    webview.start(private_mode=False)


if __name__ == '__main__':
    main()
