from flask import Flask, render_template, request, jsonify
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

def search_edamam_food(query):
    """Search for food in Edamam Food Database API"""
    try:
        # Edamam Food Database API (free tier available)
        app_id = "YOUR_EDAMAM_APP_ID"  # You'll need to get this from edamam.com
        app_key = "YOUR_EDAMAM_APP_KEY"
        
        # For now, we'll use a public nutrition analysis endpoint
        url = "https://api.edamam.com/api/food-database/v2/parser"
        params = {
            'ingr': query,
            'app_id': app_id,
            'app_key': app_key
        }
        
        headers = {
            'User-Agent': 'CalorieTracker/1.0',
            'Accept': 'application/json'
        }
        
        # If no API keys, return empty for now
        if app_id == "YOUR_EDAMAM_APP_ID":
            return []
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        products = []
        if 'hints' in data:
            for hint in data['hints'][:10]:
                food = hint.get('food', {})
                nutrients = food.get('nutrients', {})
                
                calories = nutrients.get('ENERC_KCAL', {}).get('quantity', 0)
                protein = nutrients.get('PROCNT', {}).get('quantity', 0)
                fat = nutrients.get('FAT', {}).get('quantity', 0)
                carbs = nutrients.get('CHOCDF', {}).get('quantity', 0)
                
                if calories > 0:
                    products.append({
                        'name': food.get('label', 'Unknown'),
                        'calories': float(calories),
                        'protein': float(protein),
                        'fat': float(fat),
                        'carbs': float(carbs),
                        'serving_size': 100
                    })
        
        return products
    except Exception as e:
        print(f"Error searching Edamam: {e}")
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
            'sortOrder': 'asc',
            'api_key': 'DEMO_KEY'  # USDA provides a demo key for testing
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
            for food in data['foods'][:8]:
                # Get nutrients per 100g
                nutrients = {nutrient['nutrientName']: nutrient for nutrient in food.get('foodNutrients', [])}
                
                # Energy (kcal per 100g)
                energy = nutrients.get('Energy', {}).get('value', 0)
                if energy and nutrients.get('Energy', {}).get('unitName') == 'kcal':
                    calories = energy
                elif energy and nutrients.get('Energy', {}).get('unitName') == 'kJ':
                    calories = energy / 4.184  # Convert kJ to kcal
                else:
                    calories = 0
                
                # Protein per 100g
                protein = nutrients.get('Protein', {}).get('value', 0)
                
                # Total fat per 100g
                fat = nutrients.get('Total lipid (fat)', {}).get('value', 0)
                
                # Carbohydrates per 100g
                carbs = nutrients.get('Carbohydrate, by difference', {}).get('value', 0)
                
                if calories > 0:
                    products.append({
                        'name': food.get('description', 'Unknown'),
                        'calories': float(calories),
                        'protein': float(protein) if protein is not None else 0,
                        'fat': float(fat) if fat is not None else 0,
                        'carbs': float(carbs) if carbs is not None else 0,
                        'serving_size': 100
                    })
        
        return products
    except Exception as e:
        print(f"Error searching USDA: {e}")
        return []

def search_food_by_image(image_data):
    """Search for food using image data (base64 encoded)"""
    try:
        # Decode base64 image
        if image_data.startswith('data:image'):
            # Remove data URL prefix
            image_data = image_data.split(',')[1]
        
        # For now, we'll use a simple approach - extract text from image using OCR
        # In a production environment, you might want to use a dedicated food recognition API
        # like Google Cloud Vision, AWS Rekognition, or a specialized food API
        
        # For demonstration, we'll return some mock results based on common foods
        # In a real implementation, you would use an AI vision service
        
        # Common food items for demo purposes
        common_foods = [
            {'name': 'Apple', 'calories': 52, 'protein': 0.3, 'fat': 0.2, 'carbs': 14},
            {'name': 'Banana', 'calories': 89, 'protein': 1.1, 'fat': 0.3, 'carbs': 23},
            {'name': 'Chicken Breast', 'calories': 165, 'protein': 31, 'fat': 3.6, 'carbs': 0},
            {'name': 'Rice', 'calories': 130, 'protein': 2.7, 'fat': 0.3, 'carbs': 28},
            {'name': 'Bread', 'calories': 265, 'protein': 9, 'fat': 3.2, 'carbs': 49},
            {'name': 'Eggs', 'calories': 155, 'protein': 13, 'fat': 11, 'carbs': 1.1},
            {'name': 'Salad', 'calories': 15, 'protein': 0.9, 'fat': 0.2, 'carbs': 2.9},
            {'name': 'Pizza', 'calories': 266, 'protein': 11, 'fat': 10, 'carbs': 33},
            {'name': 'Pasta', 'calories': 131, 'protein': 5.1, 'fat': 1.1, 'carbs': 25},
            {'name': 'Fish', 'calories': 206, 'protein': 22, 'fat': 12, 'carbs': 0}
        ]
        
        # In a real implementation, you would:
        # 1. Send the image to a vision API
        # 2. Get food recognition results
        # 3. Use the recognized food names to search Open Food Facts
        
        # For now, return a subset of common foods as demo results
        import random
        selected_foods = random.sample(common_foods, 3)
        
        return selected_foods
        
    except Exception as e:
        print(f"Error processing image: {e}")
        return []

@app.route('/')
def index():
    """Main page"""
    return render_template('index.html')

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
    
    # Try Edamam if available
    try:
        edamam_products = search_edamam_food(query)
        for product in edamam_products:
            name_key = product['name'].lower().strip()
            if name_key not in seen_names:
                all_products.append(product)
                seen_names.add(name_key)
                auto_save_product(product)
    except Exception as e:
        print(f"Edamam search failed: {e}")
    
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

def search_basic_foods(query):
    """Fallback search for basic/common foods"""
    query_lower = query.lower().strip()
    
    basic_foods = {
        # English
        'rice': {'name': 'Rice', 'calories': 130, 'protein': 2.7, 'fat': 0.3, 'carbs': 28},
        'brown rice': {'name': 'Brown Rice', 'calories': 111, 'protein': 2.6, 'fat': 0.9, 'carbs': 23},
        'chicken': {'name': 'Chicken Breast', 'calories': 165, 'protein': 31, 'fat': 3.6, 'carbs': 0},
        'egg': {'name': 'Eggs', 'calories': 155, 'protein': 13, 'fat': 11, 'carbs': 1.1},
        'eggs': {'name': 'Eggs', 'calories': 155, 'protein': 13, 'fat': 11, 'carbs': 1.1},
        'banana': {'name': 'Banana', 'calories': 89, 'protein': 1.1, 'fat': 0.3, 'carbs': 23},
        'apple': {'name': 'Apple', 'calories': 52, 'protein': 0.3, 'fat': 0.2, 'carbs': 14},
        'bread': {'name': 'Bread', 'calories': 265, 'protein': 9, 'fat': 3.2, 'carbs': 49},
        'pasta': {'name': 'Pasta', 'calories': 131, 'protein': 5.1, 'fat': 1.1, 'carbs': 25},
        'potato': {'name': 'Potato', 'calories': 77, 'protein': 2, 'fat': 0.1, 'carbs': 17},
        'carrot': {'name': 'Carrot', 'calories': 41, 'protein': 0.9, 'fat': 0.2, 'carbs': 10},
        'tomato': {'name': 'Tomato', 'calories': 18, 'protein': 0.9, 'fat': 0.2, 'carbs': 3.9},
        'onion': {'name': 'Onion', 'calories': 40, 'protein': 1.1, 'fat': 0.1, 'carbs': 9.3},
        'cheese': {'name': 'Cheese', 'calories': 402, 'protein': 25, 'fat': 33, 'carbs': 1.3},
        'milk': {'name': 'Milk', 'calories': 42, 'protein': 3.4, 'fat': 1, 'carbs': 5},
        'yogurt': {'name': 'Yogurt', 'calories': 59, 'protein': 10, 'fat': 0.4, 'carbs': 3.6},
        'beef': {'name': 'Beef', 'calories': 250, 'protein': 26, 'fat': 15, 'carbs': 0},
        'fish': {'name': 'Fish', 'calories': 206, 'protein': 22, 'fat': 12, 'carbs': 0},
        'salmon': {'name': 'Salmon', 'calories': 208, 'protein': 20, 'fat': 13, 'carbs': 0},
        'tuna': {'name': 'Tuna', 'calories': 144, 'protein': 30, 'fat': 1, 'carbs': 0},
        'broccoli': {'name': 'Broccoli', 'calories': 34, 'protein': 2.8, 'fat': 0.4, 'carbs': 7},
        'spinach': {'name': 'Spinach', 'calories': 23, 'protein': 2.9, 'fat': 0.4, 'carbs': 3.6},
        'avocado': {'name': 'Avocado', 'calories': 160, 'protein': 2, 'fat': 15, 'carbs': 9},
        'nuts': {'name': 'Nuts', 'calories': 607, 'protein': 20, 'fat': 54, 'carbs': 21},
        'almonds': {'name': 'Almonds', 'calories': 579, 'protein': 21, 'fat': 50, 'carbs': 22},
        'oatmeal': {'name': 'Oatmeal', 'calories': 68, 'protein': 2.4, 'fat': 1.4, 'carbs': 12},
        'beans': {'name': 'Beans', 'calories': 347, 'protein': 21, 'fat': 1.5, 'carbs': 63},
        'lentils': {'name': 'Lentils', 'calories': 116, 'protein': 9, 'fat': 0.4, 'carbs': 20},
        
        # Russian
        'ris': {'name': 'Rice', 'calories': 130, 'protein': 2.7, 'fat': 0.3, 'carbs': 28},
        'ris': {'name': 'Rice', 'calories': 130, 'protein': 2.7, 'fat': 0.3, 'carbs': 28},
        'kuritsa': {'name': 'Chicken Breast', 'calories': 165, 'protein': 31, 'fat': 3.6, 'carbs': 0},
        'kurica': {'name': 'Chicken Breast', 'calories': 165, 'protein': 31, 'fat': 3.6, 'carbs': 0},
        'yaytsa': {'name': 'Eggs', 'calories': 155, 'protein': 13, 'fat': 11, 'carbs': 1.1},
        'yaytso': {'name': 'Eggs', 'calories': 155, 'protein': 13, 'fat': 11, 'carbs': 1.1},
        'banan': {'name': 'Banana', 'calories': 89, 'protein': 1.1, 'fat': 0.3, 'carbs': 23},
        'yabloko': {'name': 'Apple', 'calories': 52, 'protein': 0.3, 'fat': 0.2, 'carbs': 14},
        'khleb': {'name': 'Bread', 'calories': 265, 'protein': 9, 'fat': 3.2, 'carbs': 49},
        'makarony': {'name': 'Pasta', 'calories': 131, 'protein': 5.1, 'fat': 1.1, 'carbs': 25},
        'kartoshka': {'name': 'Potato', 'calories': 77, 'protein': 2, 'fat': 0.1, 'carbs': 17},
        'kartofel': {'name': 'Potato', 'calories': 77, 'protein': 2, 'fat': 0.1, 'carbs': 17},
        'morkov': {'name': 'Carrot', 'calories': 41, 'protein': 0.9, 'fat': 0.2, 'carbs': 10},
        'pomidor': {'name': 'Tomato', 'calories': 18, 'protein': 0.9, 'fat': 0.2, 'carbs': 3.9},
        'luk': {'name': 'Onion', 'calories': 40, 'protein': 1.1, 'fat': 0.1, 'carbs': 9.3},
        'syir': {'name': 'Cheese', 'calories': 402, 'protein': 25, 'fat': 33, 'carbs': 1.3},
        'moloko': {'name': 'Milk', 'calories': 42, 'protein': 3.4, 'fat': 1, 'carbs': 5},
        'yogurt': {'name': 'Yogurt', 'calories': 59, 'protein': 10, 'fat': 0.4, 'carbs': 3.6},
        'govyadina': {'name': 'Beef', 'calories': 250, 'protein': 26, 'fat': 15, 'carbs': 0},
        'ryba': {'name': 'Fish', 'calories': 206, 'protein': 22, 'fat': 12, 'carbs': 0},
        'losos': {'name': 'Salmon', 'calories': 208, 'protein': 20, 'fat': 13, 'carbs': 0},
        'tunets': {'name': 'Tuna', 'calories': 144, 'protein': 30, 'fat': 1, 'carbs': 0},
        'brokkoli': {'name': 'Broccoli', 'calories': 34, 'protein': 2.8, 'fat': 0.4, 'carbs': 7},
        'shpinat': {'name': 'Spinach', 'calories': 23, 'protein': 2.9, 'fat': 0.4, 'carbs': 3.6},
        'avokado': {'name': 'Avocado', 'calories': 160, 'protein': 2, 'fat': 15, 'carbs': 9},
        'orekhi': {'name': 'Nuts', 'calories': 607, 'protein': 20, 'fat': 54, 'carbs': 21},
        'mindal': {'name': 'Almonds', 'calories': 579, 'protein': 21, 'fat': 50, 'carbs': 22},
        'ovsyanka': {'name': 'Oatmeal', 'calories': 68, 'protein': 2.4, 'fat': 1.4, 'carbs': 12},
        'fasol': {'name': 'Beans', 'calories': 347, 'protein': 21, 'fat': 1.5, 'carbs': 63},
        'chechevitsa': {'name': 'Lentils', 'calories': 116, 'protein': 9, 'fat': 0.4, 'carbs': 20},
    }
    
    # Direct match
    if query_lower in basic_foods:
        food = basic_foods[query_lower].copy()
        food['serving_size'] = 100
        return [food]
    
    # Partial match
    for key, food in basic_foods.items():
        if query_lower in key or key in query_lower:
            food_copy = food.copy()
            food_copy['serving_size'] = 100
            return [food_copy]
    
    return []

@app.route('/api/search-by-image', methods=['POST'])
def search_by_image():
    """Search for food using uploaded image"""
    try:
        data = request.json
        if not data or 'image' not in data:
            return jsonify({'error': 'No image data provided'}), 400
        
        image_data = data['image']
        products = search_food_by_image(image_data)
        
        return jsonify(products)
    except Exception as e:
        print(f"Error in search_by-image: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/products', methods=['GET', 'POST'])
def products():
    """API for products"""
    if request.method == 'GET':
        conn = get_db()
        products = conn.execute('SELECT * FROM products ORDER BY name').fetchall()
        conn.close()
        return jsonify([dict(product) for product in products])
    
    elif request.method == 'POST':
        data = request.json
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO products (name, calories, protein, fat, carbs, serving_size)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (data['name'], data['calories'], data['protein'], data['fat'], data['carbs'], data.get('serving_size', 100)))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/dishes', methods=['GET', 'POST'])
def dishes():
    """API for dishes"""
    if request.method == 'GET':
        conn = get_db()
        dishes = conn.execute('SELECT * FROM dishes').fetchall()
        conn.close()
        return jsonify([dict(dish) for dish in dishes])
    
    elif request.method == 'POST':
        data = request.json
        conn = get_db()
        cursor = conn.cursor()
        
        total_calories = 0
        total_protein = 0
        total_fat = 0
        total_carbs = 0
        total_weight = 0
        
        for ingredient in data['ingredients']:
            product = conn.execute('SELECT * FROM products WHERE id = ?', (ingredient['product_id'],)).fetchone()
            if product:
                weight = ingredient['weight']
                ratio = weight / product['serving_size']
                total_calories += product['calories'] * ratio
                total_protein += product['protein'] * ratio
                total_fat += product['fat'] * ratio
                total_carbs += product['carbs'] * ratio
                total_weight += weight
        
        cursor.execute('''
            INSERT INTO dishes (name, total_weight, total_calories, total_protein, total_fat, total_carbs)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (data['name'], total_weight, total_calories, total_protein, total_fat, total_carbs))
        
        dish_id = cursor.lastrowid
        
        for ingredient in data['ingredients']:
            cursor.execute('''
                INSERT INTO dish_ingredients (dish_id, product_id, weight)
                VALUES (?, ?, ?)
            ''', (dish_id, ingredient['product_id'], ingredient['weight']))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'dish_id': dish_id})

@app.route('/api/consumption', methods=['GET', 'POST'])
def consumption():
    """API for consumption tracking"""
    if request.method == 'GET':
        today = date.today().isoformat()
        conn = get_db()
        consumption = conn.execute('''
            SELECT c.*, p.name as product_name, d.name as dish_name
            FROM consumption c
            LEFT JOIN products p ON c.product_id = p.id
            LEFT JOIN dishes d ON c.dish_id = d.id
            WHERE c.date = ?
            ORDER BY c.id DESC
        ''', (today,)).fetchall()
        conn.close()
        return jsonify([dict(item) for item in consumption])
    
    elif request.method == 'POST':
        data = request.json
        today = date.today().isoformat()
        
        conn = get_db()
        cursor = conn.cursor()
        
        if 'product_id' in data:
            product = conn.execute('SELECT * FROM products WHERE id = ?', (data['product_id'],)).fetchone()
            if product:
                ratio = data['weight'] / product['serving_size']
                calories = product['calories'] * ratio
                protein = product['protein'] * ratio
                fat = product['fat'] * ratio
                carbs = product['carbs'] * ratio
                
                cursor.execute('''
                    INSERT INTO consumption (date, product_id, weight, calories, protein, fat, carbs)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (today, data['product_id'], data['weight'], calories, protein, fat, carbs))
        
        elif 'dish_id' in data:
            dish = conn.execute('SELECT * FROM dishes WHERE id = ?', (data['dish_id'],)).fetchone()
            if dish:
                ratio = data['weight'] / dish['total_weight']
                calories = dish['total_calories'] * ratio
                protein = dish['total_protein'] * ratio
                fat = dish['total_fat'] * ratio
                carbs = dish['total_carbs'] * ratio
                
                cursor.execute('''
                    INSERT INTO consumption (date, dish_id, weight, calories, protein, fat, carbs)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (today, data['dish_id'], data['weight'], calories, protein, fat, carbs))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/daily-summary')
def daily_summary():
    """Get daily summary"""
    today = date.today().isoformat()
    conn = get_db()
    
    consumption = conn.execute('''
        SELECT SUM(calories) as total_calories, 
               SUM(protein) as total_protein,
               SUM(fat) as total_fat,
               SUM(carbs) as total_carbs
        FROM consumption WHERE date = ?
    ''', (today,)).fetchone()
    
    goal = conn.execute('SELECT * FROM goals WHERE date = ?', (today,)).fetchone()
    
    conn.close()
    
    summary = {
        'date': today,
        'consumed': {
            'calories': consumption['total_calories'] or 0,
            'protein': consumption['total_protein'] or 0,
            'fat': consumption['total_fat'] or 0,
            'carbs': consumption['total_carbs'] or 0
        },
        'goals': {
            'calories': goal['calories'] if goal else 0,
            'protein': goal['protein'] if goal else 0,
            'fat': goal['fat'] if goal else 0,
            'carbs': goal['carbs'] if goal else 0
        }
    }
    
    return jsonify(summary)

@app.route('/api/goals', methods=['GET', 'POST'])
def goals():
    """API for goals"""
    if request.method == 'GET':
        today = date.today().isoformat()
        conn = get_db()
        goal = conn.execute('SELECT * FROM goals WHERE date = ?', (today,)).fetchone()
        conn.close()
        return jsonify(dict(goal) if goal else {})
    
    elif request.method == 'POST':
        data = request.json
        today = date.today().isoformat()
        
        conn = get_db()
        cursor = conn.cursor()
        
        existing = conn.execute('SELECT * FROM goals WHERE date = ?', (today,)).fetchone()
        
        if existing:
            cursor.execute('''
                UPDATE goals SET calories = ?, protein = ?, fat = ?, carbs = ?
                WHERE date = ?
            ''', (data['calories'], data['protein'], data['fat'], data['carbs'], today))
        else:
            cursor.execute('''
                INSERT INTO goals (date, calories, protein, fat, carbs)
                VALUES (?, ?, ?, ?, ?)
            ''', (today, data['calories'], data['protein'], data['fat'], data['carbs']))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5001)