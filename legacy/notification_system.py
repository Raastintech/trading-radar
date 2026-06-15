"""
Email Notification System - Clean Version
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os


def send_test_email():
    """Send a test email"""
    
    email = os.getenv('EMAIL_ADDRESS')
    password = os.getenv('EMAIL_PASSWORD')
    
    if not email or not password:
        print("❌ Email credentials not set!")
        print("\nRun these commands:")
        print('  export EMAIL_ADDRESS="your@gmail.com"')
        print('  export EMAIL_PASSWORD="your-app-password"')
        return False
    
    print(f"📧 Sending test email to: {email}")
    
    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "🎯 SniperTradingAI - Test Email"
        msg['From'] = email
        msg['To'] = email
        
        # HTML body
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>🎯 SniperTradingAI System Active!</h2>
            
            <p>Your trading daemon is online and ready to hunt opportunities.</p>
            
            <h3>System Status:</h3>
            <ul>
                <li>✅ Email notifications: <b>WORKING</b></li>
                <li>✅ Daemon: <b>RUNNING</b></li>
                <li>✅ Next scan: <b>Friday 9:30 AM ET</b></li>
            </ul>
            
            <h3>What to Expect:</h3>
            <ul>
                <li>📊 Scan results every 15 minutes (market hours)</li>
                <li>🎯 Trade alerts when opportunities align</li>
                <li>💰 Exit notifications when positions close</li>
                <li>📈 Daily summary at market close</li>
            </ul>
            
            <p><b>Test Time:</b> {datetime.now().strftime('%Y-%m-%d %I:%M %p')}</p>
            
            <hr>
            <p style="color: #666; font-size: 12px;">
                This is an automated test from your SniperTradingAI system.<br>
                If you received this, your email notifications are configured correctly!
            </p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        # Send via Gmail
        print("📤 Connecting to Gmail...")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            print("🔐 Logging in...")
            server.login(email, password)
            print("📨 Sending...")
            server.send_message(msg)
        
        print(f"\n✅ SUCCESS! Test email sent to {email}")
        print(f"📬 Check your inbox (may take 30 seconds)")
        return True
        
    except smtplib.SMTPAuthenticationError:
        print("\n❌ AUTHENTICATION FAILED!")
        print("\nPossible issues:")
        print("  1. Wrong app password")
        print("  2. 2-Step Verification not enabled")
        print("  3. App password has spaces (remove them)")
        print("\nDouble-check your app password!")
        return False
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return False


if __name__ == "__main__":
    print("🧪 Testing Email Notifications\n")
    send_test_email()
