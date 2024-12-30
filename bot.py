import firebase_admin
from firebase_admin import credentials, firestore
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Firebase setup
cred = credentials.Certificate("medium-b4267-firebase-adminsdk-oq9pk-e0cd08566f.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Store users with pending proofs
pending_proof_users = {}

# Initialize user in Firebase (if not already present)
def initialize_user(user_id, username):
    user_ref = db.collection("users").document(str(user_id))
    if not user_ref.get().exists:
        user_ref.set({
            "credits": 3,  # Starting credits
            "username": username or "No username set"
        })
    else:
        print(f"User {username} with ID {user_id} already exists in the database.")  # Debugging line

import re

# Function to extract username and title
def extract_details(url):
    # Regular expression to extract the username
    username_pattern = r"@([\w\d]+)"
    username_match = re.search(username_pattern, url)
    username = f"@{username_match.group(1)}" if username_match else "Unknown"

    # Split the URL to extract the title
    parts = url.split("/")
    title = parts[-1].replace("-", " ")  # Replace hyphens with spaces

    return username, title

# Check if the user is authenticated
def is_authenticated(user_id):
    user_ref = db.collection("users").document(str(user_id))
    return user_ref.get().exists

# Deduct credits from a user
def deduct_credit(user_id, reader_count):
    user_ref = db.collection("users").document(str(user_id))
    user = user_ref.get().to_dict()
    if user and user["credits"] >= reader_count:
        user_ref.update({"credits": user["credits"] - reader_count})
        return True
    return False

# Add credits to a user
def add_credit(user_id, amount):
    user_ref = db.collection("users").document(str(user_id))
    user = user_ref.get().to_dict()
    if user:
        user_ref.update({"credits": user["credits"] + amount})

# Check user credits
def get_user_credits(user_id):
    user_ref = db.collection("users").document(str(user_id))
    user = user_ref.get().to_dict()
    return user["credits"] if user else 0

# Add an article to the queue
def add_article(user_id, username, link, read_count):
    db.collection("articles").add({
        "user_id": user_id,
        "username": username or "No username set",
        "article_link": link,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "read": False,
        "read_count": read_count,
        "read_by": []
    })

# Get the next article from the queue
def get_next_article(user_id):
    # Query for articles that are unread and not yet read by the requesting user
    articles_ref = db.collection("articles").where("read", "==", False).order_by("timestamp")
    docs = articles_ref.stream()

    for doc in docs:
        article = doc.to_dict()
        # Check if the article is not submitted by the requesting user and hasn't been read by them
        if article["user_id"] != user_id and user_id not in article.get("read_by", []):
            return doc.id, article

    return None, None


# Mark an article as read
# Mark an article as read
def mark_article_as_read(article_id, user_id):
    article_ref = db.collection("articles").document(article_id)
    article = article_ref.get().to_dict()

    # Update the list of readers
    read_by = article.get("read_by", [])
    read_by.append(user_id)

    # Check if the article should be marked as fully read
    if len(read_by) >= article["read_count"]:
        article_ref.update({"read": True, "read_by": read_by})
    else:
        article_ref.update({"read_by": read_by})


# Command: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username

    initialize_user(user_id, username)
    credits = get_user_credits(user_id)
    await update.message.reply_text(f"Welcome! You have {credits} credits. Use /submit <your_link> <reader_count> to add your article to the queue, /next to get the next article to read, /balance to check your balance, or upload proof after reading an article.")

# Command: /balance
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if not is_authenticated(user_id):
        await update.message.reply_text("You need to authenticate first. Please start by typing /start.")
        return

    credits = get_user_credits(user_id)
    await update.message.reply_text(f"Your current balance is {credits} credits.")

# Command: /submit
async def submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username

    if not is_authenticated(user_id):
        await update.message.reply_text("You need to authenticate first. Please start by typing /start.")
        return

    credits = get_user_credits(user_id)
    if credits <= 0:
        await update.message.reply_text("You do not have enough credits to submit an article. Earn more credits by reading articles.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Please provide a link and reader count. Usage: /submit <your_link> <reader_count>")
        return

    link = context.args[0]
    try:
        reader_count = int(context.args[1])
        if reader_count <= 0:  # Ensure reader count is positive
            await update.message.reply_text("Reader count must be a positive number greater than zero.")
            return
    except ValueError:
        await update.message.reply_text("Reader count must be a number.")
        return

    if credits < reader_count:
        await update.message.reply_text(f"You do not have enough credits for {reader_count} readers. You have {credits} credits.")
        return

    if deduct_credit(user_id, reader_count):
        add_article(user_id, username, link, reader_count)
        await update.message.reply_text(f"Your article has been added to the queue with {reader_count} readers. You now have {credits - reader_count} credits.")
    else:
        await update.message.reply_text("Error deducting credits. Please try again.")


# Command: /next
async def next_article(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username

    if user_id in pending_proof_users:
        await update.message.reply_text("You must submit a video proof before proceeding.")
        return

    if not is_authenticated(user_id):
        await update.message.reply_text("You need to authenticate first. Please start by typing /start.")
        return

    article_id, article = get_next_article(user_id)
    if article:
        mark_article_as_read(article_id, user_id)
        pending_proof_users[user_id] = {"article_id": article_id, "owner_id": article["user_id"]}

        extracted_username, title = extract_details(article['article_link'])

        await update.message.reply_text(
            f"username: {extracted_username} \n"
            f"Title: {title}\n"
            "Please upload a video proof after reading."
        )
    else:
        await update.message.reply_text("No articles available in the queue.")

# Handler for video proof
async def handle_video_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in pending_proof_users:
        await update.message.reply_text("No proof is pending. Use /next to read an article.")
        return

    video = update.message.video
    if video:
        proof_info = pending_proof_users.pop(user_id)
        owner_id = proof_info["owner_id"]

        await context.bot.send_video(chat_id=owner_id, video=video.file_id, caption=f"Proof submitted by @{update.message.from_user.username}.")
        add_credit(user_id, 1)  # Increase credits by 1
        await update.message.reply_text("Proof submitted successfully. You have earned 1 credit. All commands are now re-enabled.")
    else:
        await update.message.reply_text("Please submit a valid video proof.")

# Main function
def main():
    application = Application.builder().token("7780083074:AAHxqFucqe6AKxh0Q4cABcd7vyOXIHCatOg").build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("submit", submit))
    application.add_handler(CommandHandler("next", next_article))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video_proof))

    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()
