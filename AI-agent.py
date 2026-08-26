import inspect
import pprint
import json
from openai import OpenAI

# Декоратор для маркировки функций как инструментов
def tool(func):
    func.is_tool = True
    return func

# 1. Функция конвертации типов данных
def python_type_to_json_type(param_type):
    type_mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean"
    }
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
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }

    properties = tool_info["function"]["parameters"]["properties"]
    required_list = tool_info["function"]["parameters"]["required"]
    
    for param_name, param in sig.parameters.items():
        if param_name == 'self':
            continue
            
        json_type = python_type_to_json_type(param.annotation)
        param_desc = get_parameter_description(docstring, param_name)
        
        properties[param_name] = {
            "type": json_type,
            "description": param_desc
        }

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
        pass

    @tool
    def find_client(self, client_name: str):
        """
        Находит клиента по названию компании.


        client_name — название компании для поиска.
        """
        clients = {
            "ООО Альфа": {
                "id": 1,
                "name": "ООО Альфа",
                "email": "info@alfa.ru"
            },
            "ООО Бета": {
                "id": 2,
                "name": "ООО Бета",
                "email": "info@beta.ru"
            }
        }
        return clients.get(client_name)

# Инициализируем инструменты и собираем их схемы
client_tools = ClientTools()
tools = get_tools(client_tools)

# Реестр инструментов для сопоставления имени со ссылкой на метод
tool_registry = {
    "create_order": client_tools.create_order,
    "find_client": client_tools.find_client
}

print("--- Сгенерированный список инструментов (tools) ---")
pprint.pprint(tools, sort_dicts=False, width=100)
print("\n" + "="*50 + "\n")


# --- ОТПРАВКА В ЛОКАЛЬНЫЙ GPT ЧЕРЕЗ OLLAMA (ШАГ 1) ---

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)

user_prompt = "Найди клиента ООО Альфа"

print(f"Отправка первого запроса: '{user_prompt}'...")
response = client.chat.completions.create(
    model="qwen2.5:3b",
    messages=[
        {"role": "user", "content": user_prompt}
    ],
    tools=tools
)

# --- ВЫПОЛНЕНИЕ ИНСТРУМЕНТА И СБОР ИСТОРИИ (ШАГ 2) ---

message = response.choices[0].message

if message.tool_calls:
    print("\n[ИИ] Обнаружен запрос на вызов функции!")
    
    tool_call = message.tool_calls[0]
    arguments = json.loads(tool_call.function.arguments)
    print(f"[ИИ] Аргументы, собранные моделью: {arguments}")
    
    tool_name = tool_call.function.name
    tool = tool_registry.get(tool_name)
    
    if tool is None:
        print("Ошибка: неизвестный инструмент")
    else:
        # Запускаем локальный метод
        result = tool(**arguments)
        print("Результат выполнения инструмента:", result)
        
        # Формируем историю сообщений для повторной отправки
        messages = [
            {
                "role": "user",
                "content": user_prompt
            },
            message  # Добавляем в историю ответ модели с её решением вызвать функцию
        ]
        
        # Добавляем результат выполнения инструмента
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result, ensure_ascii=False)
        })
        
        # --- ВТОРОЙ ЗАПРОС К МОДЕЛИ С РЕЗУЛЬТАТОМ (ШАГ 3) ---
        print("\nОтправка результата выполнения инструмента обратно в модель...")
        final_response = client.chat.completions.create(
            model="qwen2.5:3b",
            messages=messages,
            tools=tools
        )
        
        print("\n=== Итоговый ответ ИИ для пользователя ===")
        print(final_response.choices[0].message.content)
else:
    print("\nМодель вернула обычный текст вместо вызова функции:")
    print(message.content)
