export interface Job {
  id: string;
  priority: number;
}

export class Queue {
  private jobs: Job[] = [];

  push(job: Job): void {
    this.jobs.push(job);
  }

  pop(): Job | undefined {
    return this.jobs.shift();
  }

  size(): number {
    return this.jobs.length;
  }
}

export function drainQueue(queue: Queue): Job[] {
  const out: Job[] = [];
  let next = queue.pop();
  while (next) {
    out.push(next);
    next = queue.pop();
  }
  return out;
}
