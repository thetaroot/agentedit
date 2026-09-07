import { Queue, Job, drainQueue } from "../lib/queue";
import { recordMetric } from "../lib/metrics";
import { formatMoney } from "../utils/format";

export function enqueuePayment(job: Job, queue: Queue): void {
  queue.push(job);
}

export function processPayments(queue: Queue): number {
  const drained = drainQueue(queue);
  recordMetric("payments.processed", drained.length);
  return drained.length;
}

export function paymentSummary(total: number): string {
  return formatMoney(total);
}
