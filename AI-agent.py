import inspect
import pprint
import json
from openai import OpenAI
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "logistics.db"


# Перенесём клиентов в SQLite, чтобы можно было добавлять новых клиентов и заказы
def init_db():
    with sqlite3.connect(DB_PATH) as conn:

        conn.execute("PRAGMA foreign_keys = ON")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                from_city TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            )
        """)

        conn.executemany(
            """
            INSERT OR IGNORE INTO clients (id, name, email)
            VALUES (?, ?, ?)
        """,
            [(1, "ООО Альфа", "info@alfa.ru"), (2, "ООО Бета", "info@beta.ru")],
        )


# Декоратор для маркировки функций как инструментов
def tool(func):
    func.is_tool = True
    return func


# 1. Функция конвертации типов данных
def python_type_to_json_type(param_type):
    type_mapping = {str: "string", int: "integer", float: "number", bool: "boolean"}
    return type_mapping.get(param_type, "string")


# 2. Функция извлечения описания параметров из docstring
def get_parameter_description(docstring, parameter_name):
    lines = docstring.split("\n")
    for line in lines:
        cleaned_line = line.strip()
        if cleaned_line.startswith(parameter_name):
            parts = cleaned_line.split("—", 1)
            if len(parts) > 1:
                description = parts[1].strip()
                return description.capitalize().rstrip(".")
    return ""


# 3. Функция генерации схемы инструмента
def get_tool_info(tool_func):
    sig = inspect.signature(tool_func)
    docstring = inspect.getdoc(tool_func) or ""

    main_description = ""
    for line in docstring.split("\n"):
        cleaned = line.strip()
        if cleaned and not any(cleaned.startswith(p) for p in sig.parameters):
            main_description = cleaned
            break

    tool_info = {
        "type": "function",
        "function": {
            "name": tool_func.__name__,
            "description": main_description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }

    properties = tool_info["function"]["parameters"]["properties"]
    required_list = tool_info["function"]["parameters"]["required"]

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        json_type = python_type_to_json_type(param.annotation)
        param_desc = get_parameter_description(docstring, param_name)

        properties[param_name] = {"type": json_type, "description": param_desc}

        if param.default == inspect.Parameter.empty:
            required_list.append(param_name)

    return tool_info


# 4. Функция автоматического сбора всех @tool из класса
def get_tools(tool_group):
    collected_tools = []
    for name in dir(tool_group):
        tool_candidate = getattr(tool_group, name)
        if callable(tool_candidate) and getattr(tool_candidate, "is_tool", False):
            tool_schema = get_tool_info(tool_candidate)
            collected_tools.append(tool_schema)
    return collected_tools


# --- Описание класса с инструментами ---


class ClientTools:

    @tool
    def create_order(self, client_id: int, from_city: str):
        """
        Создает заказ на перевозку.

        client_id — ID клиента в CRM.
        from_city — город загрузки.
        """
        with sqlite3.connect(DB_PATH) as conn:

            conn.execute("PRAGMA foreign_keys = ON")

            conn.row_factory = sqlite3.Row

            client = conn.execute(
                """
                SELECT id, name
                FROM clients
                WHERE id = ?
            """,
                (client_id,),
            ).fetchone()

            if client is None:
                return {
                    "success": False,
                    "error": "client_not_found",
                    "message": f"Клиент с ID {client_id} не найден.",
                }

            cursor = conn.execute(
                """
                INSERT INTO orders (client_id, from_city)
                VALUES (?, ?)
            """,
                (client_id, from_city),
            )

            order_id = cursor.lastrowid

        return {
            "success": True,
            "order_id": order_id,
            "client_id": client["id"],
            "client_name": client["name"],
            "from_city": from_city,
            "status": "created",
        }

    @tool
    def find_client(self, client_name: str):
        """
        Находит клиента по названию компании.

        client_name — название компании для поиска.
        """
        with sqlite3.connect(DB_PATH) as conn:

            conn.execute("PRAGMA foreign_keys = ON")

            conn.row_factory = sqlite3.Row

            row = conn.execute(
                """
                SELECT id, name, email
                FROM clients
                WHERE name = ?
            """,
                (client_name,),
            ).fetchone()

        if row is None:
            return None

        return dict(row)


# Инициализируем инструменты и собираем их схемы
client_tools = ClientTools()
tools = get_tools(client_tools)

# Реестр инструментов для сопоставления имени со ссылкой на метод
tool_registry = {
    "create_order": client_tools.create_order,
    "find_client": client_tools.find_client,
}
init_db()
print("[База данных] Таблица заказов готова.")

# --- ОТПРАВКА В ЛОКАЛЬНЫЙ GPT ЧЕРЕЗ OLLAMA (ШАГ 1) ---

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

user_prompt = "Создай заказ на перевозку из Самары для клиента с ID 1."

print(f"Отправка первого запроса: '{user_prompt}'...")
response = client.chat.completions.create(
    model="qwen2.5:3b",
    messages=[
        {
            "role": "system",
            "content": (
                "Ты — AI-Логист. Всегда отвечай на русском языке. "
                "Используй только данные, полученные от инструментов. "
                "Не выдумывай результаты операций. "
                "Если инструмент вернул ошибку или клиент не найден, "
                "кратко и ясно сообщи об этом пользователю."
            )
        },
        {"role": "user", "content": user_prompt}
    ],
    tools=tools
)

# --- ВЫПОЛНЕНИЕ ИНСТРУМЕНТОВ И СБОР ИСТОРИИ ---

message = response.choices[0].message

if message.tool_calls:
    print("\n[ИИ] Обнаружен запрос на вызов инструмента!")

    # Сохраняем ответ модели вместе со всеми запросами инструментов
    messages = [
        {"role": "user", "content": user_prompt},
        message.model_dump(exclude_none=True),
    ]

    # Выполняем каждый вызов, который запросила модель
    for tool_call in message.tool_calls:
        tool_name = tool_call.function.name

        print(f"\n[ИИ] Выбран инструмент: {tool_name}")

        try:
            # Преобразуем JSON-строку аргументов в словарь
            arguments = json.loads(tool_call.function.arguments)
            print(f"[ИИ] Аргументы: {arguments}")

            # Ищем инструмент в реестре
            tool_func = tool_registry.get(tool_name)

            if tool_func is None:
                result = {"error": f"Неизвестный инструмент: {tool_name}"}
            else:
                # Вызываем функцию с аргументами от модели
                result = tool_func(**arguments)

            print("[Python] Результат:", result)

        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            result = {"error": f"Некорректные аргументы инструмента: {exc}"}

        except Exception as exc:
            result = {"error": f"Ошибка при выполнении инструмента: {exc}"}

        # Возвращаем результат конкретного вызова модели
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            }
        )

    # Отправляем историю вместе со всеми результатами инструментов
    print("\n[ИИ] Отправляем результаты обратно модели...")

    final_response = client.chat.completions.create(
        model="qwen2.5:3b", messages=messages, tools=tools
    )

    final_message = final_response.choices[0].message

    print("\n=== Итоговый ответ ИИ для пользователя ===")
    print(final_message.content or "Модель не вернула текстовый ответ.")

else:
    print("\nМодель вернула обычный текст:")
    print(message.content)

print("\n=== Проверка заказов в базе данных ===")

with sqlite3.connect(DB_PATH) as conn:

    conn.execute("PRAGMA foreign_keys = ON")

    cursor = conn.execute("""
        SELECT id, client_id, from_city, status, created_at
        FROM orders
        ORDER BY id DESC
    """)

    orders = cursor.fetchall()

    for order in orders:
        print(order)
