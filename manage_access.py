"""Set credentials for the initial administrator on the server console."""

import getpass

import db


def main():
    phone = input("Телефон администратора: ").strip()
    email = input("Почта администратора (gmail.com или mail.ru): ").strip()
    # Validate contacts before changing the database.
    phone = db.normalize_phone(phone)
    email = db.normalize_email(email)
    password = getpass.getpass("Новый пароль (6–64 символа): ")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпадают")
    if not 6 <= len(password.strip()) <= 64:
        raise SystemExit("Пароль от 6 до 64 символов")
    db.ensure_schema()
    db.set_access_contact(1, phone, email)
    db.set_access_password_value(1, password)
    print("Контакты и пароль администратора обновлены. Его прежние сессии отозваны.")


if __name__ == "__main__":
    main()
