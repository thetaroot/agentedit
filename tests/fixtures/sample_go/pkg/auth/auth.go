package auth

type User struct {
	ID   string
	Name string
}

var DefaultTTL = 3600

func Validate(user *User) bool {
	return user != nil && user.ID != ""
}

func Authenticate(userID string, token string) bool {
	ok := Validate(&User{ID: userID})
	return ok && token != ""
}
