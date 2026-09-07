import { Queue, Job } from "../lib/queue";
import { processPayments, enqueuePayment, paymentSummary } from "../services/payments";
import { upperFirst } from "../utils/format";

const queue = new Queue();

export function handleJobCreated(job: Job): void {
  enqueuePayment(job, queue);
}

export function handleDrainRequest(): number {
  const count = processPayments(queue);
  console.log(upperFirst(`drained ${count}`));
  return count;
}

export function buildReceipt(total: number): string {
  return paymentSummary(total);
}
