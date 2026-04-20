#!/bin/bash
curl -s -X POST http://localhost:8080/api/auth/register -H "Content-Type: application/json" -d '{"username":"Matei123","email":"test@test.com","password":"Parola123","confirmPassword":"Parola123","acceptTerms":true}'
