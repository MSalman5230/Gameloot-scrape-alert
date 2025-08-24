# Gameloot Stock Alert Bot

A Python-based web scraping application that monitors [Gameloot.in](https://gameloot.in) for PC component stock changes and sends real-time Telegram notifications when products become available or go out of stock.

## �� Features

- **Real-time Monitoring**: Continuously tracks stock changes for PC components
- **Multi-Component Support**: Monitors GPUs, CPUs, motherboards, and RAM
- **Smart Deduplication**: Automatically removes duplicate products
- **Telegram Notifications**: Instant alerts via Telegram bot for stock changes
- **MongoDB Storage**: Persistent storage of product information and stock status
- **Scheduled Scraping**: Automated scraping at configurable intervals
- **Comprehensive Logging**: Detailed logging for monitoring and debugging

## 🛠️ Supported Components

- **Graphics Cards** - Scraped every 15 minutes
- **CPUs** - Scraped every 22 minutes  
- **Motherboards** - Scraped every 27 minutes
- **RAM** - Currently disabled (can be enabled in code)

## 📋 Prerequisites

- Python 3.7+
- MongoDB instance
- Telegram Bot Token
- Internet connection for web scraping

## 🚀 Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd Gameloot-scrape-alert
   ```

2. **Install dependencies**
   ```bash
   pip install -r reqs.txt
   ```

3. **Set up environment variables**
   Create a `.env` file in the project root:
   ```env
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   LOG_FORMAT=%(levelname)s - %(message)s
   LOG_LEVEL=INFO
   ```

4. **Configure MongoDB connection**
   Update the MongoDB connection string in `scraper.py`:
   ```python
   client = pymongo.MongoClient("mongodb://username:password@host:port/?authMechanism=DEFAULT&authSource=database")
   ```

5. **Configure Telegram chat IDs**
   Update the `CHAT_IDS` list in `telegram_helper.py` with your chat IDs.

## ⚙️ Configuration

### MongoDB Setup
- Ensure MongoDB is running and accessible
- Create appropriate collections for each component type
- Verify authentication credentials

### Telegram Bot Setup
1. Create a bot via [@BotFather](https://t.me/botfather)
2. Get your bot token
3. Add the token to your `.env` file
4. Start a chat with your bot and get your chat ID
5. Add your chat ID to the `CHAT_IDS` list

### Scraping Intervals
Modify the scheduling in `scraper.py`:
```python
def task_scheduler():
    schedule.every(15).minutes.do(track_gpu)      # GPU every 15 minutes
    schedule.every(22).minutes.do(track_cpu)      # CPU every 22 minutes
    schedule.every(27).minutes.do(track_mobo)     # Motherboard every 27 minutes
    # schedule.every(27).minutes.do(track_ram)   # RAM (currently disabled)
```

## 🚀 Usage

### Start the monitoring service
```bash
python scraper.py
```

This will start the automated scheduler that continuously monitors all configured components.

### Manual scraping (for testing)
```bash
python scraper.py
# Uncomment the desired function calls at the bottom of the file
```

## 📊 How It Works

1. **Web Scraping**: The application scrapes Gameloot.in product pages using BeautifulSoup
2. **Data Processing**: Extracts product names, prices, links, and stock status
3. **Deduplication**: Removes duplicate products using smart comparison
4. **Database Comparison**: Compares current scraped data with stored MongoDB data
5. **Change Detection**: Identifies new products, restocked items, and sold-out products
6. **Notification**: Sends Telegram alerts for any stock changes
7. **Data Storage**: Updates MongoDB with current product information

## 📁 Project Structure

```
Gameloot-scrape-alert/
├── scraper.py              # Main scraping logic and scheduler
├── telegram_helper.py      # Telegram bot integration
├── logging_config.py       # Logging configuration
├── dict_list_search.py     # Utility script for performance testing
├── reqs.txt               # Python dependencies
└── README.md              # This file
```

##  Key Functions

- `scrape_product_page()`: Scrapes individual product pages
- `scrape_all_products()`: Iterates through all pages of a category
- `process_gameloot_stock()`: Main processing function for stock changes
- `send_telegram_message()`: Sends notifications via Telegram
- `task_scheduler()`: Manages automated scraping intervals

## 📝 Logging

The application provides comprehensive logging with configurable levels:
- **DEBUG**: Detailed scraping information
- **INFO**: General operation status
- **WARNING**: Non-critical issues
- **ERROR**: Critical failures

## ⚠️ Important Notes

- **Rate Limiting**: Be mindful of Gameloot.in's server resources
- **MongoDB Security**: Use strong authentication for production MongoDB instances
- **Telegram Limits**: Messages are automatically split if they exceed 4096 characters
- **Error Handling**: The application includes retry mechanisms for Telegram message sending

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is for educational and personal use. Please respect Gameloot.in's terms of service and implement appropriate rate limiting.

## 🆘 Troubleshooting

### Common Issues

1. **MongoDB Connection Failed**
   - Verify MongoDB is running
   - Check connection string and credentials
   - Ensure network connectivity

2. **Telegram Messages Not Sending**
   - Verify bot token is correct
   - Check chat IDs are valid
   - Ensure bot has permission to send messages

3. **Scraping Fails**
   - Check internet connection
   - Verify Gameloot.in is accessible
   - Review logging for specific error messages

### Getting Help

- Check the logs for detailed error information
- Verify all environment variables are set correctly
- Ensure all dependencies are installed properly

## 🔮 Future Enhancements

- Web interface for configuration
- Email notifications as alternative to Telegram
- Price change tracking and alerts
- Historical price analysis
- REST API for external integrations
