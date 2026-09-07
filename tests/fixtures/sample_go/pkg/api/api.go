package api

import (
	"example.com/samplego/pkg/auth"
)

type Controller struct {
	Auth auth.AuthValidator
}

type AuthValidator interface {
	ValidateToken(token string) bool
}

func (c *Controller) Login(userID string, token string) bool {
	return auth.Authenticate(userID, token)
}

func IsAdmin(userID string) bool {
	return len(userID) >= 5
}
