import time
import logging
import logging_config
import schedule
from dotenv import load_dotenv
from gameloot import track_gpu, track_cpu, track_mobo, track_ram

# from cex import track_cex_gpu, track_cex_cpu  # Uncomment when CEX is implemented

# Load environment variables
load_dotenv()


def task_scheduler():
    """Main task scheduler that orchestrates all scraping tasks."""
    # Gameloot tasks
    schedule.every(15).minutes.do(track_gpu)
    schedule.every(18).minutes.do(track_cpu)
    schedule.every(22).minutes.do(track_mobo)
    schedule.every(30).minutes.do(track_ram)

    # CEX tasks (uncomment when implemented)
    # schedule.every(20).minutes.do(track_cex_gpu)
    # schedule.every(25).minutes.do(track_cex_cpu)

    logging.info("Scheduler started with Jobs:")
    for jobs in schedule.get_jobs():
        print(jobs)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"Error in scheduler: {e}", exc_info=True)
        time.sleep(1)


if __name__ == "__main__":
    #task_scheduler()
    # process_gameloot_stock()
    #track_cpu()
    #track_mobo()
    #track_ram()
    #track_gpu()
