from flask import Flask, request, jsonify
import sqlite3
import requests
from datetime import date
import os
import base64
from PIL import Image
import io
import time

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Simple in-memory cache for search results
search_cache = {}
CACHE_DURATION = 300  # 5 minutes

def init_db():
    """Initialize database"""
    conn = sqlite3.connect('kbju.db')
    cursor = conn.cursor()
    
    # Products table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            calories REAL,
            protein REAL,
            fat REAL,
            carbs REAL,
            serving_size REAL DEFAULT 100
        )
    ''')
    
    # Dishes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            total_weight REAL,
            total_calories REAL,
            total_protein REAL,
            total_fat REAL,
            total_carbs REAL
        )
    ''')
    
    # Dish ingredients table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dish_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dish_id INTEGER,
            product_id INTEGER,
            weight REAL,
            FOREIGN KEY (dish_id) REFERENCES dishes (id),
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    ''')
    
    # Consumption table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS consumption (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product_id INTEGER,
            dish_id INTEGER,
            weight REAL,
            calories REAL,
            protein REAL,
            fat REAL,
            carbs REAL,
            FOREIGN KEY (product_id) REFERENCES products (id),
            FOREIGN KEY (dish_id) REFERENCES dishes (id)
        )
    ''')
    
    # Goals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            calories REAL,
            protein REAL,
            fat REAL,
            carbs REAL
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db():
    """Get database connection"""
    conn = sqlite3.connect('kbju.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_cache_key(query):
    """Generate cache key for search query"""
    return query.lower().strip()

def get_cached_search(query):
    """Get cached search results if available and not expired"""
    cache_key = get_cache_key(query)
    if cache_key in search_cache:
        cached_data, timestamp = search_cache[cache_key]
        if time.time() - timestamp < CACHE_DURATION:
            return cached_data
    return None

def cache_search_results(query, results):
    """Cache search results"""
    cache_key = get_cache_key(query)
    search_cache[cache_key] = (results, time.time())

def calculate_search_relevance(product_name, search_query):
    """Calculate relevance score for search results"""
    product_lower = product_name.lower().strip()
    query_lower = search_query.lower().strip()
    
    # Exact match gets highest score
    if product_lower == query_lower:
        return 100
    
    # Product starts with query gets very high score
    if product_lower.startswith(query_lower):
        return 90
    
    # Query is contained in product name gets high score
    if query_lower in product_lower:
        return 80
    
    # Split query into words and check matches
    query_words = query_lower.split()
    product_words = product_lower.split()
    
    # Count matching words
    matching_words = sum(1 for word in query_words if word in product_words)
    if matching_words > 0:
        # More matching words = higher score
        word_score = (matching_words / len(query_words)) * 70
        return word_score
    
    # Partial word matches
    for query_word in query_words:
        for product_word in product_words:
            if query_word in product_word or product_word in query_word:
                return 30
    
    # Very low relevance for anything else
    return 10

def sort_search_results(products, search_query):
    """Sort search results by relevance"""
    # Calculate relevance for each product
    scored_products = []
    for product in products:
        relevance = calculate_search_relevance(product['name'], search_query)
        scored_products.append({
            'product': product,
            'relevance': relevance
        })
    
    # Sort by relevance (highest first)
    scored_products.sort(key=lambda x: x['relevance'], reverse=True)
    
    # Return just the products in sorted order
    return [item['product'] for item in scored_products]

def search_food_facts(query):
    """Search for product in Open Food Facts database with maximum coverage"""
    try:
        all_products = []
        
        # Try multiple search strategies for maximum coverage
        search_strategies = [
            # Strategy 1: Exact match search (highest priority)
            {
                'url': "https://world.openfoodfacts.org/cgi/search.pl",
                'params': {
                    'search_terms': query,
                    'search_simple': 1,
                    'action': 'process',
                    'json': 1,
                    'page_size': 20,
                    'page': 1,
                    'sort_by': 'unique_scans_n',
                    'tagtype_0': 'categories',
                    'tag_contains_0': 'contains',
                    'tag_0': query
                }
            },
            # Strategy 2: Global search with popularity sort
            {
                'url': "https://world.openfoodfacts.org/cgi/search.pl",
                'params': {
                    'search_terms': query,
                    'search_simple': 1,
                    'action': 'process',
                    'json': 1,
                    'page_size': 40,
                    'page': 1,
                    'sort_by': 'unique_scans_n'
                }
            },
            # Strategy 3: English language search
            {
                'url': "https://world.openfoodfacts.org/cgi/search.pl",
                'params': {
                    'search_terms': query,
                    'search_simple': 1,
                    'action': 'process',
                    'json': 1,
                    'page_size': 25,
                    'page': 1,
                    'language': 'en'
                }
            },
            # Strategy 4: Russian language search
            {
                'url': "https://world.openfoodfacts.org/cgi/search.pl",
                'params': {
                    'search_terms': query,
                    'search_simple': 1,
                    'action': 'process',
                    'json': 1,
                    'page_size': 20,
                    'page': 1,
                    'language': 'ru'
                }
            }
        ]
        
        headers = {
            'User-Agent': 'CalorieTracker/1.0 (https://github.com/user/calorie-tracker)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8,fr;q=0.7,de;q=0.6,es;q=0.5',
            'Referer': 'https://world.openfoodfacts.org/'
        }
        
        seen_names = set()
        
        for strategy in search_strategies:
            try:
                response = requests.get(strategy['url'], params=strategy['params'], headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                if 'products' in data:
                    for product in data['products']:
                        # Extract product name with multiple fallbacks
                        name = (product.get('product_name', '') or 
                               product.get('product_name_en', '') or 
                               product.get('product_name_ru', '') or 
                               product.get('generic_name', '') or 
                               product.get('product_name_fr', '') or 
                               product.get('product_name_de', '') or 
                               'Unknown').strip()
                        
                        # Skip duplicates and unknown products
                        name_key = name.lower()
                        if name_key in seen_names or name == 'Unknown':
                            continue
                            
                        nutriments = product.get('nutriments', {})
                        
                        # Enhanced nutrient extraction with more fallbacks
                        calories = (nutriments.get('energy-kcal_100g') or 
                                   nutriments.get('energy-kcal') or 
                                   nutriments.get('energy_100g') or 
                                   nutriments.get('energy') or
                                   nutriments.get('calories_100g') or
                                   nutriments.get('calories'))
                        
                        # Convert kJ to kcal if needed
                        if calories and 'kj' in str(calories).lower():
                            calories = float(calories) / 4.184
                        
                        protein = (nutriments.get('proteins_100g') or 
                                  nutriments.get('proteins') or 
                                  nutriments.get('protein_100g') or 
                                  nutriments.get('protein') or 0)
                        
                        fat = (nutriments.get('fat_100g') or 
                              nutriments.get('fat') or 
                              nutriments.get('lipids_100g') or 
                              nutriments.get('lipids') or 0)
                        
                        carbs = (nutriments.get('carbohydrates_100g') or 
                                nutriments.get('carbohydrates') or 
                                nutriments.get('carbs_100g') or 
                                nutriments.get('carbs') or 0)
                        
                        # Only include products with calorie data
                        if calories is not None and float(calories) > 0:
                            all_products.append({
                                'name': name,
                                'calories': float(calories),
                                'protein': float(protein) if protein is not None else 0,
                                'fat': float(fat) if fat is not None else 0,
                                'carbs': float(carbs) if carbs is not None else 0,
                                'serving_size': 100
                            })
                            seen_names.add(name_key)
                            
            except Exception as e:
                print(f"Strategy {strategy} failed: {e}")
                continue
        
        return all_products[:30]  # Return up to 30 results
        
    except Exception as e:
        print(f"Error searching food facts: {e}")
        return []

def search_usda_food(query):
    """Search for food in USDA Food Data Central database"""
    try:
        # USDA Food Data Central API (requires API key, but we can use the public search)
        url = "https://api.nal.usda.gov/fdc/v1/foods/search"
        params = {
            'query': query,
            'dataType': ['Foundation', 'SR Legacy'],  # Standard reference foods
            'pageSize': 10,
            'sortBy': 'dataType.keyword',
            'api_key': 'DEMO_KEY'  # Demo key for testing
        }
        
        headers = {
            'User-Agent': 'CalorieTracker/1.0',
            'Accept': 'application/json'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        products = []
        if 'foods' in data:
            for food in data['foods']:
                nutrients = food.get('foodNutrients', [])
                
                # Find specific nutrients
                calories = 0
                protein = 0
                fat = 0
                carbs = 0
                
                for nutrient in nutrients:
                    nutrient_id = nutrient.get('nutrientId')
                    if nutrient_id == 1008:  # Energy
                        calories = nutrient.get('value', 0)
                    elif nutrient_id == 1003:  # Protein
                        protein = nutrient.get('value', 0)
                    elif nutrient_id == 1004:  # Fat
                        fat = nutrient.get('value', 0)
                    elif nutrient_id == 1005:  # Carbohydrate
                        carbs = nutrient.get('value', 0)
                
                if calories > 0:
                    products.append({
                        'name': food.get('description', 'Unknown'),
                        'calories': float(calories),
                        'protein': float(protein),
                        'fat': float(fat),
                        'carbs': float(carbs),
                        'serving_size': 100
                    })
        
        return products
    except Exception as e:
        print(f"Error searching USDA: {e}")
        return []

def search_basic_foods(query):
    """Fallback search for basic/common foods"""
    query_lower = query.lower().strip()
    
    basic_foods = {
        # English
        'rice': {'name': 'Rice', 'calories': 130, 'protein': 2.7, 'fat': 0.3, 'carbs': 28},
        'brown rice': {'name': 'Brown Rice', 'calories': 111, 'protein': 2.6, 'fat': 0.9, 'carbs': 23},
        'chicken': {'name': 'Chicken Breast', 'calories': 165, 'protein': 31, 'fat': 3.6, 'carbs': 0},
        'egg': {'name': 'Eggs', 'calories': 155, 'protein': 13, 'fat': 11, 'carbs': 1.1},
        'bread': {'name': 'Bread', 'calories': 265, 'protein': 9, 'fat': 3.2, 'carbs': 49},
        'apple': {'name': 'Apple', 'calories': 52, 'protein': 0.3, 'fat': 0.2, 'carbs': 14},
        'banana': {'name': 'Banana', 'calories': 89, 'protein': 1.1, 'fat': 0.3, 'carbs': 23},
        'potato': {'name': 'Potato', 'calories': 77, 'protein': 2, 'fat': 0.1, 'carbs': 17},
        'tomato': {'name': 'Tomato', 'calories': 18, 'protein': 0.9, 'fat': 0.2, 'carbs': 3.9},
        'onion': {'name': 'Onion', 'calories': 40, 'protein': 1.1, 'fat': 0.1, 'carbs': 9.3},
        'carrot': {'name': 'Carrot', 'calories': 41, 'protein': 0.9, 'fat': 0.2, 'carbs': 9.6},
        'cucumber': {'name': 'Cucumber', 'calories': 16, 'protein': 0.7, 'fat': 0.1, 'carbs': 3.6},
        'lettuce': {'name': 'Lettuce', 'calories': 15, 'protein': 1.4, 'fat': 0.2, 'carbs': 2.9},
        'broccoli': {'name': 'Broccoli', 'calories': 34, 'protein': 2.8, 'fat': 0.4, 'carbs': 7},
        'milk': {'name': 'Milk', 'calories': 42, 'protein': 3.4, 'fat': 1, 'carbs': 5},
        'cheese': {'name': 'Cheese', 'calories': 402, 'protein': 25, 'fat': 33, 'carbs': 1.3},
        'yogurt': {'name': 'Yogurt', 'calories': 59, 'protein': 10, 'fat': 0.4, 'carbs': 3.6},
        'beef': {'name': 'Beef', 'calories': 250, 'protein': 26, 'fat': 15, 'carbs': 0},
        'pork': {'name': 'Pork', 'calories': 242, 'protein': 27, 'fat': 14, 'carbs': 0},
        'fish': {'name': 'Fish', 'calories': 206, 'protein': 22, 'fat': 12, 'carbs': 0},
        'salmon': {'name': 'Salmon', 'calories': 208, 'protein': 20, 'fat': 13, 'carbs': 0},
        'tuna': {'name': 'Tuna', 'calories': 144, 'protein': 30, 'fat': 1, 'carbs': 0},
        'pasta': {'name': 'Pasta', 'calories': 131, 'protein': 5, 'fat': 1.1, 'carbs': 25},
        'rice cakes': {'name': 'Rice Cakes', 'calories': 382, 'protein': 8, 'fat': 2.5, 'carbs': 78},
        'oats': {'name': 'Oats', 'calories': 389, 'protein': 16.9, 'fat': 6.9, 'carbs': 66},
        'honey': {'name': 'Honey', 'calories': 304, 'protein': 0.3, 'fat': 0, 'carbs': 82},
        'butter': {'name': 'Butter', 'calories': 717, 'protein': 0.9, 'fat': 81, 'carbs': 0.1},
        'oil': {'name': 'Oil', 'calories': 884, 'protein': 0, 'fat': 100, 'carbs': 0},
        'sugar': {'name': 'Sugar', 'calories': 387, 'protein': 0, 'fat': 0, 'carbs': 100},
        'salt': {'name': 'Salt', 'calories': 0, 'protein': 0, 'fat': 0, 'carbs': 0},
        
        # Russian
        'рис': {'name': 'Рис', 'calories': 130, 'protein': 2.7, 'fat': 0.3, 'carbs': 28},
        'коричневый рис': {'name': 'Коричневый рис', 'calories': 111, 'protein': 2.6, 'fat': 0.9, 'carbs': 23},
        'курица': {'name': 'Курица', 'calories': 165, 'protein': 31, 'fat': 3.6, 'carbs': 0},
        'яйца': {'name': 'Яйца', 'calories': 155, 'protein': 13, 'fat': 11, 'carbs': 1.1},
        'хлеб': {'name': 'Хлеб', 'calories': 265, 'protein': 9, 'fat': 3.2, 'carbs': 49},
        'яблоко': {'name': 'Яблоко', 'calories': 52, 'protein': 0.3, 'fat': 0.2, 'carbs': 14},
        'банан': {'name': 'Банан', 'calories': 89, 'protein': 1.1, 'fat': 0.3, 'carbs': 23},
        'картофель': {'name': 'Картофель', 'calories': 77, 'protein': 2, 'fat': 0.1, 'carbs': 17},
        'помидор': {'name': 'Помидор', 'calories': 18, 'protein': 0.9, 'fat': 0.2, 'carbs': 3.9},
        'лук': {'name': 'Лук', 'calories': 40, 'protein': 1.1, 'fat': 0.1, 'carbs': 9.3},
        'морковь': {'name': 'Морковь', 'calories': 41, 'protein': 0.9, 'fat': 0.2, 'carbs': 9.6},
        'огурец': {'name': 'Огурец', 'calories': 16, 'protein': 0.7, 'fat': 0.1, 'carbs': 3.6},
        'салат': {'name': 'Салат', 'calories': 15, 'protein': 1.4, 'fat': 0.2, 'carbs': 2.9},
        'брокколи': {'name': 'Брокколи', 'calories': 34, 'protein': 2.8, 'fat': 0.4, 'carbs': 7},
        'молоко': {'name': 'Молоко', 'calories': 42, 'protein': 3.4, 'fat': 1, 'carbs': 5},
        'сыр': {'name': 'Сыр', 'calories': 402, 'protein': 25, 'fat': 33, 'carbs': 1.3},
        'йогурт': {'name': 'Йогурт', 'calories': 59, 'protein': 10, 'fat': 0.4, 'carbs': 3.6},
        'говядина': {'name': 'Говядина', 'calories': 250, 'protein': 26, 'fat': 15, 'carbs': 0},
        'свинина': {'name': 'Свинина', 'calories': 242, 'protein': 27, 'fat': 14, 'carbs': 0},
        'рыба': {'name': 'Рыба', 'calories': 206, 'protein': 22, 'fat': 12, 'carbs': 0},
        'лосось': {'name': 'Лосось', 'calories': 208, 'protein': 20, 'fat': 13, 'carbs': 0},
        'тунец': {'name': 'Тунец', 'calories': 144, 'protein': 30, 'fat': 1, 'carbs': 0},
        'макароны': {'name': 'Макароны', 'calories': 131, 'protein': 5, 'fat': 1.1, 'carbs': 25},
        'овсянка': {'name': 'Овсянка', 'calories': 389, 'protein': 16.9, 'fat': 6.9, 'carbs': 66},
        'мёд': {'name': 'Мёд', 'calories': 304, 'protein': 0.3, 'fat': 0, 'carbs': 82},
        'масло': {'name': 'Масло', 'calories': 717, 'protein': 0.9, 'fat': 81, 'carbs': 0.1},
        'сахар': {'name': 'Сахар', 'calories': 387, 'protein': 0, 'fat': 0, 'carbs': 100}
    }
    
    # Search for exact or partial matches
    results = []
    for key, food_data in basic_foods.items():
        if (query_lower in key or 
            key in query_lower or 
            query_lower in food_data['name'].lower() or 
            food_data['name'].lower() in query_lower):
            results.append(food_data)
    
    return results

def auto_save_product(product):
    """Automatically save found products to local database"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Check if product already exists
        cursor.execute('SELECT id FROM products WHERE LOWER(name) = LOWER(?)', (product['name'],))
        existing = cursor.fetchone()
        
        if not existing:
            # Insert new product
            cursor.execute('''
                INSERT INTO products (name, calories, protein, fat, carbs, serving_size)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (product['name'], product['calories'], product['protein'], 
                  product['fat'], product['carbs'], product.get('serving_size', 100)))
            conn.commit()
        
        conn.close()
    except Exception as e:
        print(f"Error auto-saving product: {e}")

@app.route('/')
def index():
    """Main page with embedded HTML"""
    return '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Калькулятор калорий</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }
        .modal-content { background-color: #fefefe; margin: 15% auto; padding: 20px; border: 1px solid #888; width: 80%; max-width: 500px; border-radius: 8px; }
        .close { color: #aaa; float: right; font-size: 28px; font-weight: bold; cursor: pointer; }
        .close:hover { color: black; }
    </style>
</head>
<body class="bg-gray-50">
    <div class="container mx-auto p-4 max-w-4xl">
        <h1 class="text-3xl font-bold text-center mb-8 text-blue-600">Калькулятор калорий</h1>
        
        <!-- Tabs -->
        <div class="flex space-x-4 mb-6 border-b">
            <button onclick="showTab('food')" class="tab-btn px-4 py-2 font-semibold text-blue-600 border-b-2 border-blue-600" data-tab="food">Приём пищи</button>
            <button onclick="showTab('products')" class="tab-btn px-4 py-2 text-gray-600 hover:text-blue-600" data-tab="products">Мои продукты</button>
            <button onclick="showTab('dishes')" class="tab-btn px-4 py-2 text-gray-600 hover:text-blue-600" data-tab="dishes">Блюда</button>
            <button onclick="showTab('goals')" class="tab-btn px-4 py-2 text-gray-600 hover:text-blue-600" data-tab="goals">Цели</button>
        </div>

        <!-- Food Tab -->
        <div id="food" class="tab-content active">
            <div class="grid md:grid-cols-2 gap-6">
                <!-- Search Section -->
                <div class="bg-white p-6 rounded-lg shadow">
                    <h2 class="text-xl font-semibold mb-4">Поиск продуктов</h2>
                    <div class="relative">
                        <input type="text" id="searchInput" placeholder="Введите название продукта..." 
                               class="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                        <div id="searchResults" class="absolute z-10 w-full bg-white border rounded-lg mt-1 max-h-60 overflow-y-auto hidden"></div>
                    </div>
                    <div id="selectedFood" class="mt-4 p-4 bg-blue-50 rounded-lg hidden">
                        <h3 class="font-semibold">Выбранный продукт:</h3>
                        <p id="selectedFoodName" class="text-lg"></p>
                        <div class="grid grid-cols-2 gap-4 mt-2">
                            <div>
                                <label class="block text-sm font-medium">Калории: <span id="selectedCalories"></span></label>
                            </div>
                            <div>
                                <label class="block text-sm font-medium">Белки: <span id="selectedProtein"></span></label>
                            </div>
                            <div>
                                <label class="block text-sm font-medium">Жиры: <span id="selectedFat"></span></label>
                            </div>
                            <div>
                                <label class="block text-sm font-medium">Углеводы: <span id="selectedCarbs"></span></label>
                            </div>
                        </div>
                        <div class="mt-4">
                            <label class="block text-sm font-medium">Вес (г):</label>
                            <input type="number" id="foodWeight" value="100" min="1" 
                                   class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                        </div>
                        <button onclick="addFood()" class="mt-4 w-full bg-blue-600 text-white p-3 rounded-lg hover:bg-blue-700">
                            Добавить к приёму пищи
                        </button>
                    </div>
                </div>

                <!-- Daily Intake -->
                <div class="bg-white p-6 rounded-lg shadow">
                    <h2 class="text-xl font-semibold mb-4">Сегодняшний приём</h2>
                    <div id="dailyIntake" class="space-y-3">
                        <!-- Daily food items will be added here -->
                    </div>
                    <div class="mt-6 p-4 bg-green-50 rounded-lg">
                        <h3 class="font-semibold text-green-800">Итого за день:</h3>
                        <div class="grid grid-cols-2 gap-4 mt-2">
                            <div>Калории: <span id="totalCalories" class="font-bold">0</span></div>
                            <div>Белки: <span id="totalProtein" class="font-bold">0</span>г</div>
                            <div>Жиры: <span id="totalFat" class="font-bold">0</span>г</div>
                            <div>Углеводы: <span id="totalCarbs" class="font-bold">0</span>г</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Products Tab -->
        <div id="products" class="tab-content">
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-semibold mb-4">Мои продукты</h2>
                <div class="mb-4">
                    <button onclick="showAddProductModal()" class="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700">
                        + Добавить продукт
                    </button>
                </div>
                <div id="productsList" class="space-y-3">
                    <!-- Products will be loaded here -->
                </div>
            </div>
        </div>

        <!-- Dishes Tab -->
        <div id="dishes" class="tab-content">
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-semibold mb-4">Мои блюда</h2>
                <div class="mb-4">
                    <button onclick="showAddDishModal()" class="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700">
                        + Создать блюдо
                    </button>
                </div>
                <div id="dishesList" class="space-y-3">
                    <!-- Dishes will be loaded here -->
                </div>
            </div>
        </div>

        <!-- Goals Tab -->
        <div id="goals" class="tab-content">
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-semibold mb-4">Мои цели по питанию</h2>
                <div class="grid md:grid-cols-2 gap-6">
                    <div>
                        <h3 class="font-semibold mb-3">Установить цели</h3>
                        <div class="space-y-3">
                            <div>
                                <label class="block text-sm font-medium">Калории:</label>
                                <input type="number" id="goalCalories" placeholder="2000" 
                                       class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                            </div>
                            <div>
                                <label class="block text-sm font-medium">Белки (г):</label>
                                <input type="number" id="goalProtein" placeholder="150" 
                                       class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                            </div>
                            <div>
                                <label class="block text-sm font-medium">Жиры (г):</label>
                                <input type="number" id="goalFat" placeholder="65" 
                                       class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                            </div>
                            <div>
                                <label class="block text-sm font-medium">Углеводы (г):</label>
                                <input type="number" id="goalCarbs" placeholder="300" 
                                       class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                            </div>
                            <button onclick="setGoals()" class="w-full bg-blue-600 text-white p-3 rounded-lg hover:bg-blue-700">
                                Сохранить цели
                            </button>
                        </div>
                    </div>
                    <div>
                        <h3 class="font-semibold mb-3">Текущие цели</h3>
                        <div id="currentGoals" class="p-4 bg-gray-50 rounded-lg">
                            <!-- Current goals will be displayed here -->
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Add Product Modal -->
    <div id="addProductModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="hideAddProductModal()">&times;</span>
            <h2 class="text-xl font-semibold mb-4">Добавить продукт</h2>
            <div class="space-y-3">
                <div>
                    <label class="block text-sm font-medium">Название:</label>
                    <input type="text" id="newProductName" placeholder="Название продукта" 
                           class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium">Калории:</label>
                        <input type="number" id="newProductCalories" placeholder="100" 
                               class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium">Белки (г):</label>
                        <input type="number" id="newProductProtein" placeholder="20" 
                               class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium">Жиры (г):</label>
                        <input type="number" id="newProductFat" placeholder="5" 
                               class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium">Углеводы (г):</label>
                        <input type="number" id="newProductCarbs" placeholder="10" 
                               class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-4">
                    <button onclick="hideAddProductModal()" class="bg-gray-300 text-gray-700 p-3 rounded-lg hover:bg-gray-400">
                        Отмена
                    </button>
                    <button onclick="addProduct()" class="bg-green-600 text-white p-3 rounded-lg hover:bg-green-700">
                        Добавить
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let selectedFood = null;
        let dailyFoods = [];

        function showTab(tabName) {
            // Hide all tabs
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('text-blue-600', 'border-b-2', 'border-blue-600');
                btn.classList.add('text-gray-600');
            });
            
            // Show selected tab
            document.getElementById(tabName).classList.add('active');
            document.querySelector(`[data-tab="${tabName}"]`).classList.remove('text-gray-600');
            document.querySelector(`[data-tab="${tabName}"]`).classList.add('text-blue-600', 'border-b-2', 'border-blue-600');
            
            // Load data for tab
            if (tabName === 'products') loadProducts();
            if (tabName === 'dishes') loadDishes();
            if (tabName === 'goals') loadGoals();
        }

        function searchFood() {
            const query = document.getElementById('searchInput').value.trim();
            if (query.length < 2) {
                document.getElementById('searchResults').classList.add('hidden');
                return;
            }

            fetch(`/api/search?q=${encodeURIComponent(query)}`)
                .then(response => response.json())
                .then(products => {
                    displaySearchResults(products);
                })
                .catch(error => {
                    console.error('Error searching:', error);
                });
        }

        function displaySearchResults(products) {
            const resultsDiv = document.getElementById('searchResults');
            
            if (products.length === 0) {
                resultsDiv.innerHTML = '<div class="p-3 text-gray-500">Ничего не найдено</div>';
            } else {
                resultsDiv.innerHTML = products.map(product => `
                    <div onclick="selectFood(${JSON.stringify(product).replace(/"/g, '&quot;')})" 
                         class="p-3 hover:bg-gray-100 cursor-pointer border-b">
                        <div class="font-semibold">${product.name}</div>
                        <div class="text-sm text-gray-600">
                            ${product.calories} ккал | Б: ${product.protein}г | Ж: ${product.fat}г | У: ${product.carbs}г
                        </div>
                    </div>
                `).join('');
            }
            
            resultsDiv.classList.remove('hidden');
        }

        function selectFood(product) {
            selectedFood = product;
            document.getElementById('selectedFood').classList.remove('hidden');
            document.getElementById('selectedFoodName').textContent = product.name;
            document.getElementById('selectedCalories').textContent = product.calories;
            document.getElementById('selectedProtein').textContent = product.protein;
            document.getElementById('selectedFat').textContent = product.fat;
            document.getElementById('selectedCarbs').textContent = product.carbs;
            document.getElementById('searchResults').classList.add('hidden');
            document.getElementById('searchInput').value = product.name;
        }

        function addFood() {
            if (!selectedFood) {
                alert('Пожалуйста, выберите продукт');
                return;
            }

            const weight = parseFloat(document.getElementById('foodWeight').value);
            if (!weight || weight <= 0) {
                alert('Пожалуйста, введите корректный вес');
                return;
            }

            const multiplier = weight / selectedFood.serving_size;
            const foodItem = {
                ...selectedFood,
                weight: weight,
                calories: Math.round(selectedFood.calories * multiplier),
                protein: Math.round(selectedFood.protein * multiplier * 10) / 10,
                fat: Math.round(selectedFood.fat * multiplier * 10) / 10,
                carbs: Math.round(selectedFood.carbs * multiplier * 10) / 10
            };

            dailyFoods.push(foodItem);
            updateDailyIntake();
            
            // Reset selection
            selectedFood = null;
            document.getElementById('selectedFood').classList.add('hidden');
            document.getElementById('searchInput').value = '';
            document.getElementById('foodWeight').value = 100;
        }

        function updateDailyIntake() {
            const intakeDiv = document.getElementById('dailyIntake');
            intakeDiv.innerHTML = dailyFoods.map((food, index) => `
                <div class="flex justify-between items-center p-3 bg-gray-50 rounded">
                    <div>
                        <div class="font-semibold">${food.name}</div>
                        <div class="text-sm text-gray-600">${food.weight}г | ${food.calories} ккал</div>
                    </div>
                    <button onclick="removeFood(${index})" class="text-red-500 hover:text-red-700">
                        Удалить
                    </button>
                </div>
            `).join('');

            // Calculate totals
            const totals = dailyFoods.reduce((acc, food) => ({
                calories: acc.calories + food.calories,
                protein: acc.protein + food.protein,
                fat: acc.fat + food.fat,
                carbs: acc.carbs + food.carbs
            }), { calories: 0, protein: 0, fat: 0, carbs: 0 });

            document.getElementById('totalCalories').textContent = totals.calories;
            document.getElementById('totalProtein').textContent = totals.protein;
            document.getElementById('totalFat').textContent = totals.fat;
            document.getElementById('totalCarbs').textContent = totals.carbs;
        }

        function removeFood(index) {
            dailyFoods.splice(index, 1);
            updateDailyIntake();
        }

        function showAddProductModal() {
            document.getElementById('addProductModal').style.display = 'block';
        }

        function hideAddProductModal() {
            document.getElementById('addProductModal').style.display = 'none';
        }

        function addProduct() {
            const name = document.getElementById('newProductName').value.trim();
            const calories = parseFloat(document.getElementById('newProductCalories').value);
            const protein = parseFloat(document.getElementById('newProductProtein').value) || 0;
            const fat = parseFloat(document.getElementById('newProductFat').value) || 0;
            const carbs = parseFloat(document.getElementById('newProductCarbs').value) || 0;

            if (!name || !calories) {
                alert('Пожалуйста, введите название и калории');
                return;
            }

            fetch('/api/products', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: name,
                    calories: calories,
                    protein: protein,
                    fat: fat,
                    carbs: carbs
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    hideAddProductModal();
                    loadProducts();
                    // Clear form
                    document.getElementById('newProductName').value = '';
                    document.getElementById('newProductCalories').value = '';
                    document.getElementById('newProductProtein').value = '';
                    document.getElementById('newProductFat').value = '';
                    document.getElementById('newProductCarbs').value = '';
                }
            })
            .catch(error => {
                console.error('Error adding product:', error);
            });
        }

        function loadProducts() {
            fetch('/api/products')
                .then(response => response.json())
                .then(products => {
                    const productsDiv = document.getElementById('productsList');
                    productsDiv.innerHTML = products.map(product => `
                        <div class="flex justify-between items-center p-3 bg-gray-50 rounded">
                            <div>
                                <div class="font-semibold">${product.name}</div>
                                <div class="text-sm text-gray-600">
                                    ${product.calories} ккал | Б: ${product.protein}г | Ж: ${product.fat}г | У: ${product.carbs}г
                                </div>
                            </div>
                            <button onclick="deleteProduct(${product.id})" class="text-red-500 hover:text-red-700">
                                Удалить
                            </button>
                        </div>
                    `).join('');
                });
        }

        function deleteProduct(id) {
            if (confirm('Вы уверены, что хотите удалить этот продукт?')) {
                fetch(`/api/products/${id}`, {
                    method: 'DELETE'
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        loadProducts();
                    }
                });
            }
        }

        function loadDishes() {
            fetch('/api/dishes')
                .then(response => response.json())
                .then(dishes => {
                    const dishesDiv = document.getElementById('dishesList');
                    dishesDiv.innerHTML = dishes.map(dish => `
                        <div class="p-4 bg-gray-50 rounded">
                            <div class="font-semibold">${dish.name}</div>
                            <div class="text-sm text-gray-600">
                                ${dish.total_calories} ккал | Б: ${dish.total_protein}г | Ж: ${dish.total_fat}г | У: ${dish.total_carbs}г
                            </div>
                            <div class="text-sm text-gray-600">Общий вес: ${dish.total_weight}г</div>
                        </div>
                    `).join('');
                });
        }

        function loadGoals() {
            fetch('/api/goals')
                .then(response => response.json())
                .then(goals => {
                    const goalsDiv = document.getElementById('currentGoals');
                    if (goals && goals.length > 0) {
                        const goal = goals[0];
                        goalsDiv.innerHTML = `
                            <div>Калории: ${goal.calories}</div>
                            <div>Белки: ${goal.protein}г</div>
                            <div>Жиры: ${goal.fat}г</div>
                            <div>Углеводы: ${goal.carbs}г</div>
                        `;
                    } else {
                        goalsDiv.innerHTML = '<div class="text-gray-500">Цели не установлены</div>';
                    }
                });
        }

        function setGoals() {
            const calories = parseFloat(document.getElementById('goalCalories').value) || 0;
            const protein = parseFloat(document.getElementById('goalProtein').value) || 0;
            const fat = parseFloat(document.getElementById('goalFat').value) || 0;
            const carbs = parseFloat(document.getElementById('goalCarbs').value) || 0;

            fetch('/api/goals', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    calories: calories,
                    protein: protein,
                    fat: fat,
                    carbs: carbs
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    loadGoals();
                    // Clear form
                    document.getElementById('goalCalories').value = '';
                    document.getElementById('goalProtein').value = '';
                    document.getElementById('goalFat').value = '';
                    document.getElementById('goalCarbs').value = '';
                }
            });
        }

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            // Load today's consumption
            const today = new Date().toISOString().split('T')[0];
            fetch(`/api/consumption?date=${today}`)
                .then(response => response.json())
                .then(data => {
                    if (data.success && data.consumption) {
                        dailyFoods = data.consumption;
                        updateDailyIntake();
                    }
                });

            // Setup search
            const searchInput = document.getElementById('searchInput');
            let searchTimeout;
            
            searchInput.addEventListener('input', function() {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(searchFood, 300);
            });

            // Hide search results when clicking outside
            document.addEventListener('click', function(e) {
                if (!e.target.closest('#searchInput') && !e.target.closest('#searchResults')) {
                    document.getElementById('searchResults').classList.add('hidden');
                }
            });
        });
    </script>
</body>
</html>
    '''

@app.route('/api/search')
def search_products():
    """Search products across multiple internet APIs with caching and auto-save"""
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    
    # Check cache first
    cached_results = get_cached_search(query)
    if cached_results:
        return jsonify(cached_results)
    
    all_products = []
    seen_names = set()  # To avoid duplicates
    
    # Try Open Food Facts first (most comprehensive)
    try:
        off_products = search_food_facts(query)
        for product in off_products:
            name_key = product['name'].lower().strip()
            if name_key not in seen_names:
                all_products.append(product)
                seen_names.add(name_key)
                # Auto-save to local database for future use
                auto_save_product(product)
    except Exception as e:
        print(f"Open Food Facts search failed: {e}")
    
    # Try USDA as backup
    try:
        usda_products = search_usda_food(query)
        for product in usda_products:
            name_key = product['name'].lower().strip()
            if name_key not in seen_names:
                all_products.append(product)
                seen_names.add(name_key)
                auto_save_product(product)
    except Exception as e:
        print(f"USDA search failed: {e}")
    
    # If still no results, try basic food matching as last resort
    if len(all_products) == 0:
        basic_matches = search_basic_foods(query)
        for product in basic_matches:
            all_products.append(product)
            auto_save_product(product)
    
    # Sort results by relevance
    sorted_results = sort_search_results(all_products, query)
    
    # Cache the results
    final_results = sorted_results[:20]  # Return up to 20 results
    cache_search_results(query, final_results)
    
    return jsonify(final_results)

@app.route('/api/products', methods=['GET', 'POST'])
def products():
    """Handle products CRUD operations"""
    if request.method == 'GET':
        conn = get_db()
        products = conn.execute('SELECT * FROM products ORDER BY name').fetchall()
        conn.close()
        return jsonify([dict(product) for product in products])
    
    elif request.method == 'POST':
        data = request.get_json()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO products (name, calories, protein, fat, carbs)
            VALUES (?, ?, ?, ?, ?)
        ''', (data['name'], data['calories'], data['protein'], data['fat'], data['carbs']))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def delete_product(product_id):
    """Delete a product"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM products WHERE id = ?', (product_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/dishes', methods=['GET', 'POST'])
def dishes():
    """Handle dishes CRUD operations"""
    if request.method == 'GET':
        conn = get_db()
        dishes = conn.execute('SELECT * FROM dishes ORDER BY name').fetchall()
        conn.close()
        return jsonify([dict(dish) for dish in dishes])
    
    elif request.method == 'POST':
        data = request.get_json()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO dishes (name, total_weight, total_calories, total_protein, total_fat, total_carbs)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (data['name'], data['total_weight'], data['total_calories'], 
              data['total_protein'], data['total_fat'], data['total_carbs']))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/consumption', methods=['GET', 'POST'])
def consumption():
    """Handle daily consumption tracking"""
    if request.method == 'GET':
        date = request.args.get('date', str(date.today()))
        conn = get_db()
        consumption = conn.execute('''
            SELECT p.name, c.weight, c.calories, c.protein, c.fat, c.carbs
            FROM consumption c
            JOIN products p ON c.product_id = p.id
            WHERE c.date = ?
            ORDER BY c.id
        ''', (date,)).fetchall()
        conn.close()
        return jsonify({'success': True, 'consumption': [dict(item) for item in consumption]})
    
    elif request.method == 'POST':
        data = request.get_json()
        today = str(date.today())
        conn = get_db()
        cursor = conn.cursor()
        
        # First, save the product if it doesn't exist
        cursor.execute('''
            INSERT OR IGNORE INTO products (name, calories, protein, fat, carbs)
            VALUES (?, ?, ?, ?, ?)
        ''', (data['name'], data['calories'], data['protein'], data['fat'], data['carbs']))
        
        # Get the product ID
        cursor.execute('SELECT id FROM products WHERE name = ?', (data['name'],))
        product = cursor.fetchone()
        
        # Add to consumption
        cursor.execute('''
            INSERT INTO consumption (date, product_id, weight, calories, protein, fat, carbs)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (today, product['id'], data['weight'], data['calories'], 
              data['protein'], data['fat'], data['carbs']))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/goals', methods=['GET', 'POST'])
def goals():
    """Handle nutrition goals"""
    if request.method == 'GET':
        conn = get_db()
        goals = conn.execute('SELECT * FROM goals ORDER BY date DESC LIMIT 1').fetchall()
        conn.close()
        return jsonify([dict(goal) for goal in goals])
    
    elif request.method == 'POST':
        data = request.get_json()
        today = str(date.today())
        conn = get_db()
        cursor = conn.cursor()
        
        # Update or insert today's goals
        cursor.execute('''
            INSERT OR REPLACE INTO goals (date, calories, protein, fat, carbs)
            VALUES (?, ?, ?, ?, ?)
        ''', (today, data['calories'], data['protein'], data['fat'], data['carbs']))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5001))
    debug_mode = os.environ.get('DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
