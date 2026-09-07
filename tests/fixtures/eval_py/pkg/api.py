from pkg.format import upper_first
from pkg.queue import Job, Queue
from pkg.services import enqueue_payment, payment_summary, process_payments


def handle_job_created(job: Job, queue: Queue) -> None:
    enqueue_payment(job, queue)


def handle_drain_request(queue: Queue) -> int:
    count = process_payments(queue)
    print(upper_first(f"drained {count}"))
    return count


def build_receipt(total: int) -> str:
    return payment_summary(total)
