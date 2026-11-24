"""
WooCommerce REST API v3 client for product management
"""

import requests
import logging
from typing import Dict, List, Optional, Any
from requests.auth import HTTPBasicAuth
from mimetypes import guess_type

logger = logging.getLogger(__name__)


class WooCommerceClient:
    """WooCommerce REST API v3 client"""
    
    def __init__(self, store_url: str, consumer_key: str, consumer_secret: str):
        """
        Initialize WooCommerce client
        
        Args:
            store_url: WooCommerce store base URL (e.g., https://shop.example.com)
            consumer_key: WooCommerce REST API consumer key
            consumer_secret: WooCommerce REST API consumer secret
        """
        self.store_url = store_url.rstrip('/')
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.api_url = f"{self.store_url}/wp-json/wc/v3"
        self.auth = HTTPBasicAuth(consumer_key, consumer_secret)
        self.timeout = 30
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make authenticated request to WooCommerce API"""
        url = f"{self.api_url}{endpoint}"
        
        try:
            response = requests.request(
                method=method,
                url=url,
                auth=self.auth,
                timeout=self.timeout,
                **kwargs
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"WooCommerce API error: {e.response.status_code} - {e.response.text}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {str(e)}")
            raise
    
    def create_product(self, product_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new WooCommerce product
        
        Args:
            product_data: Product data dict (name, description, price, sku, images, etc.)
            
        Returns:
            Product data from WooCommerce API
        """
        logger.info(f"Creating product: {product_data.get('name')}")
        
        return self._make_request(
            'POST',
            '/products',
            json=product_data
        )

    def upload_media(self, filename, photo, username, app_password, *, include_url=False):
        """Upload an image to the WordPress media library."""
        if not self.store_url.startswith('https://'):
            raise ValueError('Use an HTTPS store URL for WordPress media uploads.')
        response = requests.post(
            f'{self.store_url}/wp-json/wp/v2/media',
            auth=HTTPBasicAuth(username, app_password),
            files={'file': (filename, photo, guess_type(filename)[0] or 'application/octet-stream')},
            timeout=self.timeout,
        )
        response.raise_for_status()
        media = response.json()
        return (media['id'], media['source_url']) if include_url else media['id']

    def check_product_access(self):
        products = self._make_request('GET', '/products', params={'per_page': 1, '_fields': 'id'}, allow_redirects=False)
        if not isinstance(products, list):
            raise ValueError('WooCommerce did not return a product list.')

    def check_media_access(self, username, app_password):
        response = requests.get(
            f'{self.store_url}/wp-json/wp/v2/users/me',
            auth=HTTPBasicAuth(username, app_password),
            params={'context': 'edit', '_fields': 'capabilities'},
            timeout=self.timeout, allow_redirects=False,
        )
        response.raise_for_status()
        capabilities = response.json().get('capabilities', {})
        return isinstance(capabilities, dict) and capabilities.get('upload_files') is True
    
    def update_product(self, product_id: int, product_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing WooCommerce product"""
        logger.info(f"Updating product {product_id}")
        
        return self._make_request(
            'PUT',
            f'/products/{product_id}',
            json=product_data
        )
    
    def get_product(self, product_id: int) -> Dict[str, Any]:
        """Retrieve product by ID"""
        return self._make_request('GET', f'/products/{product_id}')
    
    def delete_product(self, product_id: int, force: bool = True) -> Dict[str, Any]:
        """
        Delete a product
        
        Args:
            product_id: Product ID
            force: Permanently delete (True) or move to trash (False)
        """
        return self._make_request(
            'DELETE',
            f'/products/{product_id}',
            params={'force': force}
        )
    
    def list_products(self, page: int = 1, per_page: int = 100, **filters) -> Dict[str, Any]:
        """
        List products with optional filters
        
        Args:
            page: Page number
            per_page: Items per page
            **filters: Additional query parameters (search, sku, category, etc.)
        """
        params = {
            'page': page,
            'per_page': per_page,
            **filters
        }
        return self._make_request('GET', '/products', params=params)
    
    def search_products(self, search_term: str) -> List[Dict[str, Any]]:
        """Search products by keyword"""
        result = self._make_request(
            'GET',
            '/products',
            params={'search': search_term}
        )
        return result.get('data', []) if isinstance(result, dict) else result
    
    def get_product_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        """Find product by SKU"""
        products = self._make_request(
            'GET',
            '/products',
            params={'sku': sku}
        )
        
        if isinstance(products, list) and products:
            return products[0]
        elif isinstance(products, dict):
            data = products.get('data', []) or products.get('results', [])
            return data[0] if data else None
        
        return None
    
    def create_product_attribute(self, attribute_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a product attribute (like size, color, etc.)"""
        logger.info(f"Creating attribute: {attribute_data.get('name')}")
        
        return self._make_request(
            'POST',
            '/products/attributes',
            json=attribute_data
        )
    
    def list_attributes(self) -> List[Dict[str, Any]]:
        """List all product attributes"""
        return self._make_request('GET', '/products/attributes')
    
    def get_attribute(self, attribute_id: int) -> Dict[str, Any]:
        """Get attribute by ID"""
        return self._make_request('GET', f'/products/attributes/{attribute_id}')
    
    def create_attribute_term(self, attribute_id: int, term_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create an attribute term (e.g., "Blue" for color attribute)
        
        Args:
            attribute_id: Parent attribute ID
            term_data: Term data (name, slug, etc.)
        """
        return self._make_request(
            'POST',
            f'/products/attributes/{attribute_id}/terms',
            json=term_data
        )
    
    def get_categories(self) -> List[Dict[str, Any]]:
        """List all product categories"""
        return self._make_request('GET', '/products/categories')
    
    def create_category(self, category_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a product category"""
        return self._make_request(
            'POST',
            '/products/categories',
            json=category_data
        )
    
    def batch_update_products(self, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Batch update multiple products
        
        Args:
            updates: List of product update dicts (must include 'id')
        """
        payload = {'update': updates}
        return self._make_request(
            'POST',
            '/products/batch',
            json=payload
        )
    
    def health_check(self) -> bool:
        """Check if WooCommerce API is accessible"""
        try:
            self._make_request('GET', '/system/status')
            return True
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return False
