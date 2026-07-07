"""SMTP疎通確認（単体）。実行例:

    $env:SMTP_HOST = "smtp.gmail.com"
    $env:SMTP_PORT = "587"
    $env:SMTP_USER = "your-account@gmail.com"
    $env:SMTP_PASSWORD = "アプリパスワード16桁"
    $env:MAIL_TO = "your-account@gmail.com"
    python smtp_test.py

このスクリプトは環境変数からのみ資格情報を読む。パスワードをコードや会話に書かないこと。
"""
import os

from src.notifier import send_mail

cfg = {
    "host": os.environ.get("SMTP_HOST", ""),
    "port": os.environ.get("SMTP_PORT", "587"),
    "user": os.environ.get("SMTP_USER", ""),
    "password": os.environ.get("SMTP_PASSWORD", ""),
    "mail_from": os.environ.get("MAIL_FROM", ""),
    "mail_to": os.environ.get("MAIL_TO", ""),
}

missing = [k for k in ("host", "user", "password", "mail_to") if not cfg[k]]
if missing:
    print(f"未設定の環境変数があります: {missing}")
    raise SystemExit(1)

ok = send_mail(cfg, "trend-watcher SMTP疎通テスト", "このメールが届けば設定は正常です。", False, print)
print("送信結果:", "OK" if ok else "失敗（上のログを確認）")
