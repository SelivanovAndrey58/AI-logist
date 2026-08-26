import inspect
import pprint
import json
from openai import OpenAI


# =====================================================================
# 1. АВТОМАТИЗАЦИЯ СБОРА ИНСТРУМЕНТОВ (TOOL CALLING ENGINE)
# =====================================================================
def tool(func):
    """
    Декоратор для маркировки методов класса как доступных инструментов для ИИ.
    Добавляет функции специальный флаг `is_tool = True`.
    """
    func.is_tool = True
    return func

# Функция конвертации типов данных
def python_type_to_json_type(param_type):
    """
    Конвертирует строгие типы данных Python в стандартные типы JSON Schema.
    Это необходимо, так как LLM-модели принимают описание инструментов в формате JSON.
    """
    type_mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean"
    }
    # Если тип не найден в маппинге, по умолчанию возвращаем "string"
    return type_mapping.get(param_type, "string")

# Функция извлечения описания параметров из docstring
def get_parameter_description(docstring, parameter_name):
    """
    Парсит docstring (документацию) функции, чтобы извлечь описание конкретного параметра.
    Ищет строку, начинающуюся с имени параметра, и забирает текст после разделителя '—'.
    """
    lines = docstring.split("\n")
    for line in lines:
        cleaned_line = line.strip()
        if cleaned_line.startswith(parameter_name):
            parts = cleaned_line.split("—", 1)
            if len(parts) > 1:
                description = parts[1].strip()
                # Форматируем текст: делаем первую букву заглавной и убираем точку на конце
                return description.capitalize().rstrip(".")
    return ""

# Функция генерации схемы инструмента
def get_tool_info(tool_func):
       """
    Главная функция генерации JSON-схемы инструмента (Function Specification).
    Использует модуль `inspect` для динамического анализа сигнатуры функции (параметров и их типов).
    """
    sig = inspect.signature(tool_func)
    docstring = inspect.getdoc(tool_func) or ""
    # Извлекаем главное описание функции (первую непустую строку до описания параметров)
    main_description = ""
    for line in docstring.split("\n"):
        cleaned = line.strip()
        if cleaned and not any(cleaned.startswith(p) for p in sig.parameters):
            main_description = cleaned
            break
            
    # Базовый каркас схемы, который требует OpenAI / Ollama API
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

    # Динамически разбираем каждый аргумент функции
    for param_name, param in sig.parameters.items():
        if param_name == 'self': # Пропускаем ссылку на экземпляр класса
            continue
            
        json_type = python_type_to_json_type(param.annotation)
        param_desc = get_parameter_description(docstring, param_name)

        # Заполняем свойства аргумента в схему
        properties[param_name] = {
            "type": json_type,
            "description": param_desc
        }
        # Если у параметра нет дефолтного значения, он добавляется в список обязательных (required)
        if param.default == inspect.Parameter.empty:
            required_list.append(param_name)
            
    return tool_info

# Функция автоматического сбора всех @tool из класса
def get_tools(tool_group):
    """
    Сканирует переданный класс (объект) и автоматически собирает схемы 
    для всех методов, у которых есть декоратор `@tool`.
    """
    collected_tools = []
    for name in dir(tool_group):
        tool_candidate = getattr(tool_group, name)
        # Проверяем, что это функция и у нее есть наш флаг
        if callable(tool_candidate) and getattr(tool_candidate, "is_tool", False):
            tool_schema = get_tool_info(tool_candidate)
            collected_tools.append(tool_schema)
    return collected_tools

# =====================================================================
# 2. ОПИСАНИЕ БИЗНЕС-ЛОГИКИ И ИНСТРУМЕНТОВ ЛОГИСТИКИ
# =====================================================================

class ClientTools:
    """
    Класс, содержащий реальные функции (инструменты), которые наш ИИ-агент 
    сможет вызывать для решения задач автоматизации логистики.
    """
    @tool
    def create_order(self, client_id: int, from_city: str):
        """
        Создает заказ на перевозку.


        client_id — ID клиента в CRM.
        from_city — город загрузки.
        """
        # Имитация интеграции с ERP/CRM системой логистики
        pass

    @tool
    def find_client(self, client_name: str):
        """
        Находит клиента по названию компании.


        client_name — название компании для поиска.
        """
        # Имитация базы данных (Mock-данные) клиентов компании
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
# Автоматически генерируем список JSON-схем для передачи в LLM
tools = get_tools(client_tools)

# Реестр (маппинг) для сопоставления строкового имени функции от ИИ с реальным методом в Python
tool_registry = {
    "create_order": client_tools.create_order,
    "find_client": client_tools.find_client
}
# Выводим сгенерированные схемы в консоль для визуальной проверки структуры
print("--- Сгенерированный список инструментов (tools) ---")
pprint.pprint(tools, sort_dicts=False, width=100)
print("\n" + "="*50 + "\n")


# =====================================================================
# 3. ВЗАИМОДЕЙСТВИЕ С ЛОКАЛЬНОЙ МОДЕЛЬЮ ЧЕРЕЗ OLLAMA
# =====================================================================
# Настраиваем клиент для работы с Ollama (используем совместимый с OpenAI формат API)
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama" # Для Ollama ключ может быть любой строкой
)

user_prompt = "Найди клиента ООО Альфа"

# --- ШАГ 1: Отправляем запрос пользователя и список доступных инструментов в LLM ---
print(f"Отправка первого запроса: '{user_prompt}'...")
response = client.chat.completions.create(
    model="qwen2.5:3b", # Используем легкую локальную модель Qwen
    messages=[
        {"role": "user", "content": user_prompt}
    ],
    tools=tools # Передаем сгенерированные схемы инструментов
)

message = response.choices[0].message

# --- ШАГ 2: Анализируем ответ модели на предмет вызова функций (Tool Calls) ---

if message.tool_calls:
    print("\n[ИИ] Обнаружен запрос на вызов функции!")
    # Извлекаем первый вызов функции из списка, предложенного моделью
    tool_call = message.tool_calls[0]
    # Парсим аргументы, которые ИИ извлек из текста пользователя (переводим из JSON-строки в dict)
    arguments = json.loads(tool_call.function.arguments)
    print(f"[ИИ] Аргументы, собранные моделью: {arguments}")

    # Ищем имя функции в нашем локальном реестре Python
    tool_name = tool_call.function.name
    tool = tool_registry.get(tool_name)
    
    if tool is None:
        print("Ошибка: неизвестный инструмент")
    else:
        # Выполняем реальный Python-код, распаковывая аргументы из ИИ (**arguments)
        result = tool(**arguments)
        print("Результат выполнения инструмента:", result)
        
        # Формируем цепочку контекста (историю сообщений) для повторного запроса.
        # Это критически важно: модель должна знать свой предыдущий ответ и то, что функция выполнена.
        messages = [
            {
                "role": "user",
                "content": user_prompt
            },
            message  # Обязательно передаем ответ модели с флагом tool_calls
        ]
        
        # Добавляем в историю результат работы функции с типом роли "tool"
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result, ensure_ascii=False)
        })
        
        # --- ШАГ 3: Отправляем историю с результатом выполнения функции обратно в LLM ---
        print("\nОтправка результата выполнения инструмента обратно в модель...")
        final_response = client.chat.completions.create(
            model="qwen2.5:3b",
            messages=messages,
            tools=tools
        )
        # Выводим итоговый текстовый ответ, который модель сгенерировала на основе данных из функции
        print("\n=== Итоговый ответ ИИ для пользователя ===")
        print(final_response.choices[0].message.content)
else:
    # Если модель не сочла нужным вызывать функции, выводим обычный текстовый ответ
    print("\nМодель вернула обычный текст вместо вызова функции:")
    print(message.content)
