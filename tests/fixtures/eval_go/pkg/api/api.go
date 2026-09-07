package api

import (
	"example.com/evalgo/pkg/format"
	"example.com/evalgo/pkg/service"
)

func HandleJobCreated(item string) bool {
	return service.EnqueuePayment(item)
}

func HandleDrainRequest() int {
	return service.ProcessPayments()
}

func BuildReceipt(total int) string {
	return format.UpperFirst(service.PaymentSummary(total))
}
