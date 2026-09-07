package queue

func Enqueue(item string) bool {
	return item != ""
}

func Drain() []string {
	return nil
}
