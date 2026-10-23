import { useEffect, useMemo, useState } from "react";

type Product = {
  id: number;
  sku: string;
  name: string;
  category: string;
  selling_price: string;
  cost_price: string;
  stock_quantity: number;
  reorder_level: number;
  is_active: boolean;
};

type Customer = {
  id: number;
  name: string;
  phone: string;
  email: string;
  loyalty_points: number;
  is_active: boolean;
};

type Sale = {
  id: number;
  invoice_number: string;
  customer_name: string;
  sold_at: string;
  subtotal: string;
  tax_amount: string;
  total: string;
  status: string;
};

type DashboardSummary = {
  total_revenue: number;
  total_products: number;
  active_customers: number;
  low_stock_alerts: number;
  sales_count: number;
  recent_sales: Sale[];
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api/v1";

function formatCurrency(value: number | string): string {
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [selectedProductId, setSelectedProductId] = useState<number | "">("");
  const [selectedCustomerId, setSelectedCustomerId] = useState<number | "">("");
  const [quantity, setQuantity] = useState(1);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const productOptions = useMemo(
    () => products.filter((product) => product.is_active !== false),
    [products],
  );

  const selectedProduct = products.find((product) => product.id === Number(selectedProductId)) ?? null;

  const refreshData = async () => {
    const [productResponse, customerResponse, dashboardResponse] = await Promise.all([
      fetchJson<Product[]>("/products/"),
      fetchJson<Customer[]>("/customers/"),
      fetchJson<DashboardSummary>("/dashboard/"),
    ]);

    setProducts(productResponse);
    setCustomers(customerResponse);
    setSummary(dashboardResponse);
  };

  useEffect(() => {
    void refreshData().catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : "Unable to load POS data.");
    });
  }, []);

  const handleQuickSale = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!selectedProductId) {
      setMessage("Select a product before creating a sale.");
      return;
    }

    setLoading(true);
    try {
      await fetchJson("/sales/", {
        method: "POST",
        body: JSON.stringify({
          customer: selectedCustomerId || null,
          items: [{ product: Number(selectedProductId), quantity }],
        }),
      });
      setMessage(`Sale created for ${selectedProduct?.name} x ${quantity}.`);
      setSelectedCustomerId("");
      setSelectedProductId("");
      setQuantity(1);
      await refreshData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to create the sale.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-7xl p-6">
        <header className="mb-8 flex items-center justify-between gap-4 border-b border-slate-700 pb-4">
          <div>
            <p className="text-sm uppercase tracking-[0.32em] text-cyan-400">Enterprise POS</p>
            <h1 className="mt-2 text-3xl font-bold">Operations dashboard</h1>
          </div>
          <div className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-sm text-emerald-300">
            Live system status
          </div>
        </header>

        <section className="mb-8 grid gap-4 md:grid-cols-4">
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-4 shadow-lg shadow-slate-950/30">
            <p className="text-sm text-slate-400">Total revenue</p>
            <p className="mt-4 text-3xl font-semibold text-cyan-300">
              ${formatCurrency(summary?.total_revenue ?? 0)}
            </p>
          </div>
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-4 shadow-lg shadow-slate-950/30">
            <p className="text-sm text-slate-400">Products</p>
            <p className="mt-4 text-3xl font-semibold text-violet-300">{summary?.total_products ?? 0}</p>
          </div>
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-4 shadow-lg shadow-slate-950/30">
            <p className="text-sm text-slate-400">Active customers</p>
            <p className="mt-4 text-3xl font-semibold text-emerald-300">{summary?.active_customers ?? 0}</p>
          </div>
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-4 shadow-lg shadow-slate-950/30">
            <p className="text-sm text-slate-400">Low stock alerts</p>
            <p className="mt-4 text-3xl font-semibold text-amber-300">{summary?.low_stock_alerts ?? 0}</p>
          </div>
        </section>

        <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-xl font-semibold">Products</h2>
              <span className="text-sm text-slate-400">{products.length} records</span>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead className="text-slate-400">
                  <tr>
                    <th className="pb-3 pr-6">SKU</th>
                    <th className="pb-3 pr-6">Name</th>
                    <th className="pb-3 pr-6">Price</th>
                    <th className="pb-3 pr-6">Stock</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((product) => (
                    <tr key={product.id} className="border-t border-slate-800">
                      <td className="py-3 pr-6 text-slate-300">{product.sku}</td>
                      <td className="py-3 pr-6">{product.name}</td>
                      <td className="py-3 pr-6">${formatCurrency(product.selling_price)}</td>
                      <td className={`py-3 pr-6 ${product.stock_quantity <= product.reorder_level ? "text-amber-300" : "text-emerald-300"}`}>
                        {product.stock_quantity}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <form onSubmit={handleQuickSale} className="rounded-2xl border border-slate-700 bg-slate-900 p-5">
            <h2 className="mb-4 text-xl font-semibold">Quick sale</h2>
            <div className="space-y-4">
              <label className="block">
                <span className="mb-2 block text-sm text-slate-400">Product</span>
                <select
                  value={selectedProductId}
                  onChange={(event) => setSelectedProductId(event.target.value === "" ? "" : Number(event.target.value))}
                  className="w-full rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-slate-100 outline-none transition focus:border-cyan-500"
                >
                  <option value="">Select a product</option>
                  {productOptions.map((product) => (
                    <option key={product.id} value={product.id}>
                      {product.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-2 block text-sm text-slate-400">Customer</span>
                <select
                  value={selectedCustomerId}
                  onChange={(event) => setSelectedCustomerId(event.target.value === "" ? "" : Number(event.target.value))}
                  className="w-full rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-slate-100 outline-none transition focus:border-cyan-500"
                >
                  <option value="">Walk-in</option>
                  {customers.map((customer) => (
                    <option key={customer.id} value={customer.id}>
                      {customer.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-2 block text-sm text-slate-400">Quantity</span>
                <input
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(event) => setQuantity(Math.max(1, Number(event.target.value) || 1))}
                  className="w-full rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-slate-100 outline-none transition focus:border-cyan-500"
                />
              </label>

              <div className="rounded-xl bg-slate-800/70 p-3 text-sm text-slate-300">
                {selectedProduct ? (
                  <>
                    <div className="flex items-center justify-between">
                      <span>Unit price</span>
                      <span>${formatCurrency(selectedProduct.selling_price)}</span>
                    </div>
                    <div className="mt-2 flex items-center justify-between font-medium text-cyan-300">
                      <span>Estimated total (incl. 10% tax)</span>
                      <span>${formatCurrency(Number(selectedProduct.selling_price) * quantity * 1.1)}</span>
                    </div>
                  </>
                ) : (
                  <span>Select a product to calculate the total.</span>
                )}
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-xl bg-cyan-500 px-4 py-2 font-medium text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? "Saving sale..." : "Create sale"}
              </button>
            </div>

            {message && <p className="mt-4 rounded-xl border border-slate-700 bg-slate-800 p-3 text-sm text-slate-200">{message}</p>}
          </form>
        </div>

        <section className="mt-8 rounded-2xl border border-slate-700 bg-slate-900 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold">Recent sales</h2>
            <span className="text-sm text-slate-400">{summary?.sales_count ?? 0} total</span>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="pb-3 pr-6">Invoice</th>
                  <th className="pb-3 pr-6">Customer</th>
                  <th className="pb-3 pr-6">Date</th>
                  <th className="pb-3 pr-6">Total</th>
                </tr>
              </thead>
              <tbody>
                {(summary?.recent_sales ?? []).map((sale) => (
                  <tr key={sale.id} className="border-t border-slate-800">
                    <td className="py-3 pr-6 text-cyan-300">{sale.invoice_number}</td>
                    <td className="py-3 pr-6">{sale.customer_name}</td>
                    <td className="py-3 pr-6">{new Date(sale.sold_at).toLocaleDateString()}</td>
                    <td className="py-3 pr-6 text-emerald-300">${formatCurrency(sale.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

export default App;
