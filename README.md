# Denim Studio

Denim Studio is a Django workspace for turning garment photos and product notes into reviewed WooCommerce listings. It uses Django REST Framework, LangGraph and OpenRouter. Local development can use SQLite, and the Docker Compose setup uses PostgreSQL.

## Start the workspace

Use Python 3.11 or later in a fresh environment.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
$env:DEBUG = 'True'
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

Use the existing administrator if one is already set up. Open the [catalog workspace](http://127.0.0.1:8001/pipeline/dashboard/) or [Django administration](http://127.0.0.1:8001/admin/). Both require a staff account. For local Docker development, configure `.env` with `DJANGO_DEBUG=True`, run `docker compose up -d --build`, then run `docker compose exec web python manage.py createsuperuser` for a new installation. Compose serves the dashboard on `127.0.0.1` at the port set by `DJANGO_PORT` in `.env`, which defaults to `8000`. PostgreSQL is available to Django on the Compose network.

Django loads integration settings from `.env`. Process environment variables take priority. The database uses SQLite unless database environment variables are supplied by the process, as Docker Compose does. Serve configured media and static files separately when deploying with `DEBUG=False`.

## Create a reusable model profile

1. Open **Model profiles** in the workspace or **Add a model profile** in Django administration.
2. Add a clear full-body front photo and a full-body side photo of the same person. **Additional photos** accepts up to three more photos in one batch. Use unfiltered photos with even lighting and a clear face. Updating the batch keeps the front and side fields. For a fictional sample identity and help making both poses, use [FASHN Studio Create Model](https://help.fashn.ai/using-fashn/studio/create-model).
3. Add any styling details that should stay consistent across photos, such as hair or shoes.
4. Save the profile and select it on a new product. The app builds reusable identity instructions from the reference-photo slots and your notes locally. Creating, editing or previewing a model profile makes zero AI calls.

A product keeps the photos and profile instructions selected at intake so later profile changes apply to new jobs. The garment images define the jeans and the selected model photos define the person. The side view also uses a crop of the generated front image as a secondary identity reference. If the configured reference limit would be exceeded, the app combines extra model angles into a contact sheet without dropping garment photos. Compare likeness and garment details at review, because the image model can still alter features.

### Full-frame photo editing

The existing `MODEL_TRYON_PROVIDER=openrouter_masked` setting uses the original pose photo as the base for a complete edit. Save the front and side photos in **Model profiles** and select that profile for a product. Each shot asks the image provider to change only the trousers, including the waistband, while preserving the pose, face, clothing and lighting. The app saves the provider's complete output to avoid a visible seam across the waist. Review the likeness and denim fit before publishing.

The default provider is OpenRouter with Seedream 5.0 Pro at 1K. To change the OpenRouter image model, edit `OPENROUTER_IMAGE_MODEL` in `.env` and restart Django. A draft missing a pose photo can pick up an updated profile before its first generation.

### Switch image generation to Gemini

1. Create a [Google AI Studio API key](https://aistudio.google.com/apikey) in a project with access to image generation. Direct Gemini API billing is configured for the Cloud project separately from a Google AI Pro subscription. [Gemini API billing](https://aistudio.google.com/docs/billing)
2. Set `GEMINI_API_KEY` in `.env`. You can keep `GEMINI_IMAGE_MODEL=gemini-nano-banana-2.1` or set `gemini-3-pro-image` for the Pro image model. Restart Django. [Google image generation models](https://aistudio.google.com/docs/image-generation)
3. In **Image generation**, choose **Gemini** and save. This setting applies to future generation requests on this dashboard. Switch it back to **OpenRouter** at any time. Text extraction, chart reading, WooCommerce and the local fabric crop keep their existing connections.

To try a Gemini image model using the existing OpenRouter key, keep **OpenRouter** selected and set `OPENROUTER_IMAGE_MODEL=google/gemini-3.1-flash-image` before restarting. The existing OpenRouter configuration is the default. [OpenRouter Gemini image editing](https://openrouter.ai/blog/tutorials/nano-banana/)

### Optional FASHN virtual try-on

To use the optional alternate provider, set `MODEL_TRYON_PROVIDER=fashn` and `FASHN_API_KEY` in `.env` and restart Django. Keep `OPENROUTER_API_KEY` for product copy and the flat lay. The `openrouter` setting selects the older reference-guided workflow.

1. In **Model profiles**, upload the exact head-to-toe front and side photos for the same person. Use visible waistbands, relaxed arms, even light, the same top and shoes, and an uncluttered background. Use the bulk photos as extra views for reference-guided generation or visual review. Each FASHN request uses only its selected pose photo. Profile creation stores references and does not fine-tune a model.
2. In **New product**, choose that profile and upload a clear front photo of the actual jeans first. A garment-only photo on a plain background is ideal. Additional garment views are used by the flat lay generation.
3. Generate for one product and compare the front and side against the source photos before approving. Each shot asks FASHN to edit the supplied person photo and replace only the trousers. The request asks it to preserve the face, hair, body proportions, pose, top, shoes, light and background. If a status check fails after FASHN accepts a prediction, retrying polls the saved prediction ID.

[FASHN Try-On Max documentation](https://docs.fashn.ai/api-reference/tryon-max)

## Prepare and publish one product

1. Enter the product name, SKU, price, stock, fit, wash and any confirmed material details. Upload up to seven photos of the same garment, including front, back and any detail angles. Photos must be JPEG, PNG or WebP and up to 10 MB each. The first garment photo is the default source for the fabric crop unless you upload a fabric reference.
2. Click **Extract product details**. One vision request reads the notes and up to two garment photos and drafts original product copy, SEO fields, Size and Color. Sizes come from the notes or the assigned chart. Verify inferred color and fill absent commercial values before publishing.
3. Enter optional **Special generation notes**, then click **Save and generate images**. Every garment photo is sent to the image model for each of the three shots. The side shot also receives the selected model references and the generated front shot. LangGraph runs separate steps for reference preparation, each shot, the local fabric crop and review. The progress panel retains a timestamped log. New images are saved under a product-named folder, for example `generated_images/atelier-indigo-jeans-7f3c97d1/atelier-indigo-jeans-model-front.jpg`.
4. Review the images in catalog order: Model front, Model side, Garment front and back, Fabric close-up. Use **Regenerate photo** for an AI-generated image. Regenerating the front prepares a new front and side pair together, and keeps the old pair if either request fails. Use **Adjust crop** for the original fabric close-up. Select a size chart if available, then approve locally or publish.
5. If an image request fails, **Retry missing images** reuses the completed shots and resumes at the missing shot. Image generation and retries can incur charges. The normal run uses one extraction request and three image requests, with no extra profile-analysis or image QA request.

## Size charts and social campaigns

- Under **Size charts**, upload an existing chart image, enter a comma-separated measurement table, or choose **Read photo into table** for a handwritten chart. Photo reading makes one OpenRouter vision request using `OPENROUTER_CHART_MODEL`. Check all values and replace unreadable cells marked `?` before saving. Measurement charts use the same HTML table layout and keep a locally generated PNG for the WooCommerce custom fields.
- Select a chart during product review or choose **Add size chart** to create and select one in the review. Save the product to apply the assignment. **Show size chart in WooCommerce product description** controls whether the storefront description includes the chart. The assignment and its image URL or WordPress attachment ID are available via `denim_size_chart_image_url` and `denim_size_chart_image_id`.
- Open **Social campaign** in product review or after publishing. Edit the suggested captions and add an optional PNG logo for Facebook and Instagram posts. Choose a platform, review the images and caption, and confirm. Use **Download campaign** with a PNG logo for a ZIP of watermarked images and captions for manual posting.

### Update a published listing

Open **Product queue**, choose the published product and edit its details or size chart. **Save edits** keeps a local draft. **Update WooCommerce listing** sends the saved fields to the original WooCommerce product ID. Use **Regenerate photo**, **Upload photo** or **Adjust crop** to update images. Image changes are local until you update the listing. The update replaces the changed gallery positions and keeps other store photos and the listing's status.

If the product was deleted in WooCommerce, open its dashboard entry and use **Remove if deleted from store**. The app removes the dashboard entry only after confirming the saved product ID is deleted or in the store trash. This check does not delete a live WooCommerce listing or any media files.

To replace a chart, select a different chart. To remove its assignment, choose **No size chart**. With description display enabled, a measurement chart appears as an HTML table and an uploaded chart appears as an image. A separate chart button beside the size picker depends on the WordPress theme or size chart plugin.

### Connect Facebook and Instagram

1. In your Meta app, set the App ID and App Secret as `META_APP_ID` and `META_APP_SECRET`. Set `META_GRAPH_API_VERSION` to a version supported by the app. [Meta Business SDK guide](https://developers.facebook.com/documentation/business-sdk/getting-started)
2. Set `FACEBOOK_PAGE_ID` and `FACEBOOK_PAGE_ACCESS_TOKEN` from the Page you want to manage. Grant `pages_manage_posts`, `pages_read_engagement` and `pages_show_list`. The Page user needs the Create Content task. [Meta Page Photos API](https://developers.facebook.com/docs/graph-api/reference/page/photos/)
3. For a linked Instagram professional account, also grant `instagram_basic` and `instagram_content_publish` to the Page token and leave `INSTAGRAM_ACCESS_TOKEN` blank. For Instagram Login, grant `instagram_business_basic` and `instagram_business_content_publish` and set `INSTAGRAM_ACCOUNT_ID` and `INSTAGRAM_ACCESS_TOKEN`. [Meta Content Publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
4. For Instagram photos, provide a public HTTPS `PUBLIC_MEDIA_BASE_URL` serving the Django media directory or configure WordPress media upload.
5. Reload Django after editing the settings in .env. In **Social connections**, select **Check social accounts** to verify the connected accounts. Use the publish buttons in product review once your accounts have publishing permission.

### Connect TikTok

1. Enable Content Posting API in your TikTok developer app and get approval for `video.upload`. Use Login Kit to authorize the target account for `video.upload` and `user.info.basic`. Set the user access token in `TIKTOK_ACCESS_TOKEN`. Refresh it when it expires and reload Django after replacing it. [TikTok upload setup](https://developers.tiktok.com/doc/content-posting-api-get-started-upload-content/) · [User access tokens](https://developers.tiktok.com/doc/oauth-user-access-token-management/)
2. Verify the HTTPS photo URL prefix in your TikTok app and set `TIKTOK_MEDIA_BASE_URL`. The default `TIKTOK_MEDIA_SOURCE=storage` uses the public URL of the Django media directory. For a local Docker setup, set `TIKTOK_MEDIA_SOURCE=wordpress` to use your configured WordPress media library. Verify the uploads URL prefix for that library. [TikTok media transfer guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/)
3. In **Social campaign**, select **Send to TikTok inbox** and confirm. **Check TikTok status** tracks delivery. Open the TikTok inbox notification to review the photos and publish the draft. The TikTok upload uses photos without a logo overlay. [TikTok sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines/)

**Use suggested captions** refreshes the text from the saved product details. Use `SOCIAL_CURRENCY` to label prices and `SOCIAL_BRAND_NAME` for a brand hashtag. Add sizes to the product attributes or the assigned chart.

## Connect services

- Set `OPENROUTER_API_KEY` for extraction and images. The default models are [GPT OSS 20B](https://openrouter.ai/openai/gpt-oss-20b) and [Seedream 5.0 Pro](https://openrouter.ai/bytedance-seed/seedream-5-0-pro).
- Set the HTTPS `WOOCOMMERCE_URL`, `WOOCOMMERCE_CONSUMER_KEY` and `WOOCOMMERCE_CONSUMER_SECRET` with product read/write access.
- For localhost, also set `WORDPRESS_MEDIA_USER` and `WORDPRESS_MEDIA_APP_PASSWORD` for an account that can upload to the [WordPress media library](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/). The app uses the uploaded media IDs when publishing. Alternatively, set `PUBLIC_MEDIA_BASE_URL` to the publicly reachable HTTPS URL of the generated media directory's parent (for example, `https://studio.example.com/media/`). WooCommerce needs access to those URLs to import the photos.

### Connect WordPress from localhost

1. In the WordPress admin for the destination store, open **Users → Profile** for a user who can upload to the Media Library. Under **Application Passwords**, enter **Denim Studio** and select **Add New Application Password**. Copy the generated value and the account's WordPress login username. [WordPress instructions](https://developer.wordpress.org/advanced-administration/security/application-passwords/)
2. In **WooCommerce → Settings → Advanced → REST API**, create a **Read/Write** key for a user who can publish products, or reuse an existing key with those permissions. Use the store's HTTPS base address, without `/wp-admin` or `/wp-json`. [WooCommerce instructions](https://woocommerce.com/document/woocommerce-rest-api/)
3. Set these values in the project's local `.env`, then restart Django.

```dotenv
WOOCOMMERCE_URL=https://shop.example.com
WOOCOMMERCE_CONSUMER_KEY=ck_your_key
WOOCOMMERCE_CONSUMER_SECRET=cs_your_secret
WORDPRESS_MEDIA_USER=your-wordpress-username
WORDPRESS_MEDIA_APP_PASSWORD="xxxx xxxx xxxx xxxx xxxx xxxx"
PUBLIC_MEDIA_BASE_URL=
```

4. In Denim Studio, open **Store connection → Test connection**. It checks WooCommerce product read access and the WordPress user's media upload capability. It makes no test uploads or listings. If Application Passwords is missing in WordPress, check that the site uses HTTPS and that your site administrator or security plugin allows the feature.

## Test without credits

```powershell
python manage.py test catalog_pipeline
```

The automated run uses a separate test database, synthetic photos and mocked OpenRouter, WordPress and WooCommerce responses. It verifies profile creation and updating, review before generation, reference ordering, image names and dimensions, resuming partial image generation, approval and publication.


Set-Location -LiteralPath 'G:\MY Project\DJANGO Project\auto\auto'
docker compose up -d
docker compose ps
http://127.0.0.1:8002/admin/login/?next=/pipeline/dashboard/
