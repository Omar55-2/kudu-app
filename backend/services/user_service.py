"""
Employee (User) business logic: create/search/update/delete. This mirrors
the original PersonService's behavior (workplace lookup-or-create, hobby
lookup-or-create, city/country handling, filterable search) but rewritten
against the SQLAlchemy User model instead of raw SQL.
"""
from extensions import db
from config import DEPARTMENTS, ROLES
from models import User, Workplace, Hobby, Ticket, Comment


def find_or_create_workplace(company_name):
    if not company_name:
        return None
    workplace = Workplace.query.filter_by(company_name=company_name).first()
    if not workplace:
        workplace = Workplace(company_name=company_name)
        db.session.add(workplace)
        db.session.flush()
    return workplace


def find_or_create_hobbies(hobby_names):
    hobbies = []
    for name in hobby_names or []:
        name = name.strip()
        if not name:
            continue
        hobby = Hobby.query.filter_by(hobby_name=name).first()
        if not hobby:
            hobby = Hobby(hobby_name=name)
            db.session.add(hobby)
            db.session.flush()
        hobbies.append(hobby)
    return hobbies


def create_employee(data):
    """Creates an employee/user. Raises ValueError on bad input."""
    email = (data.get("email") or "").strip().lower()
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    password = data.get("password")
    department = data.get("department")

    if not first_name or not last_name or not email or not password:
        raise ValueError("first_name, last_name, email and password are required")
    if department not in DEPARTMENTS:
        raise ValueError(f"department must be one of {DEPARTMENTS}")
    if User.query.filter_by(email=email).first():
        raise ValueError("Email already registered")

    role = data.get("role", "employee")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")

    is_first_user = User.query.count() == 0
    workplace = find_or_create_workplace(data.get("company_name"))
    hobbies = find_or_create_hobbies(data.get("hobbies"))

    user = User(
        first_name=first_name, last_name=last_name, email=email,
        department=department, role="admin" if is_first_user else role,
        age=data.get("age"), position=data.get("position"),
        salary=data.get("salary", 0), city=data.get("city"), country=data.get("country"),
        workplace=workplace, hobbies=hobbies,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def search_employees(filters, page=1, per_page=10):
    query = User.query

    if filters.get("id"):
        query = query.filter(User.id == filters["id"])
    if filters.get("first_name"):
        query = query.filter(User.first_name.ilike(f"%{filters['first_name']}%"))
    if filters.get("last_name"):
        query = query.filter(User.last_name.ilike(f"%{filters['last_name']}%"))
    if filters.get("city"):
        query = query.filter(User.city.ilike(f"%{filters['city']}%"))
    if filters.get("position"):
        query = query.filter(User.position.ilike(f"%{filters['position']}%"))
    if filters.get("department"):
        query = query.filter(User.department == filters["department"])
    if filters.get("role"):
        query = query.filter(User.role == filters["role"])
    if filters.get("company_name"):
        query = query.join(Workplace).filter(Workplace.company_name.ilike(f"%{filters['company_name']}%"))
    if filters.get("hobby"):
        query = query.join(User.hobbies).filter(Hobby.hobby_name.ilike(f"%{filters['hobby']}%"))

    total_records = query.distinct().count()
    employees = (
        query.distinct().order_by(User.id)
        .offset((page - 1) * per_page).limit(per_page).all()
    )
    return [e.to_dict(full=True) for e in employees], total_records


def update_employee(employee_id, data):
    user = db.session.get(User, employee_id)
    if not user:
        raise LookupError("Employee not found")

    if "first_name" in data:
        user.first_name = data["first_name"]
    if "last_name" in data:
        user.last_name = data["last_name"]
    if "age" in data:
        user.age = data["age"]
    if "position" in data:
        user.position = data["position"]
    if "salary" in data:
        user.salary = data["salary"]
    if "city" in data:
        user.city = data["city"]
    if "country" in data:
        user.country = data["country"]
    if "department" in data:
        if data["department"] not in DEPARTMENTS:
            raise ValueError(f"department must be one of {DEPARTMENTS}")
        user.department = data["department"]
    if "role" in data:
        if data["role"] not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        user.role = data["role"]
    if "is_active" in data:
        user.is_active = bool(data["is_active"])
    if "company_name" in data:
        user.workplace = find_or_create_workplace(data["company_name"])
    if "hobbies" in data:
        user.hobbies = find_or_create_hobbies(data["hobbies"])

    db.session.commit()
    return user


def delete_employee(employee_id):
    """Deletes an employee. To avoid silently destroying ticket history,
    an employee who created or was assigned any ticket cannot be deleted
    until those tickets are reassigned/reported on - this is the simplest
    safe rule that doesn't require a cascading-delete decision tree."""
    user = db.session.get(User, employee_id)
    if not user:
        raise LookupError("Employee not found")

    has_tickets = (
        Ticket.query.filter(db.or_(Ticket.created_by_id == employee_id, Ticket.assigned_to_id == employee_id)).first()
        or Comment.query.filter_by(user_id=employee_id).first()
    )
    if has_tickets:
        raise ValueError(
            "This employee has ticket history (created, assigned, or commented). "
            "Reassign their tickets first, or deactivate the account instead of deleting it."
        )

    user.hobbies = []  # clears the user_hobbies association rows
    db.session.delete(user)
    db.session.commit()
