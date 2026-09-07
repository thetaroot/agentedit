package service

import (
	"example.com/evalgo/pkg/format"
	"example.com/evalgo/pkg/metrics"
	"example.com/evalgo/pkg/queue"
)

func EnqueuePayment(item string) bool {
	return queue.Enqueue(item)
}

func ProcessPayments() int {
	items := queue.Drain()
	metrics.RecordMetric("processed", len(items))
	return len(items)
}

func PaymentSummary(total int) string {
	return format.FormatMoney(total, "EUR")
}

// keepImportsAlive references every import so removing a single exported
// function can never orphan an import in this file (which would make the
// removal itself un-compilable and hide real caller breakage from the oracle).
func keepImportsAlive() int {
	_ = queue.Enqueue("x")
	_ = queue.Drain()
	metrics.RecordMetric("keep", 0)
	_ = format.FormatMoney(0, "EUR")
	return 0
}
