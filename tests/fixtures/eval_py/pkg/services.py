from pkg.format import format_money
from pkg.metrics import record_metric
from pkg.queue import Queue, drain_queue
from pkg.types import Job


def enqueue_payment(job: Job, queue: Queue) -> None:
    queue.push(job)


def process_payments(queue: Queue) -> int:
    drained = drain_queue(queue)
    record_metric("payments.processed", len(drained))
    return len(drained)


def payment_summary(total: int) -> str:
    return format_money(total)
